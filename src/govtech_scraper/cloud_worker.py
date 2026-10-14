"""Bounded Railway cron worker. Default dry-run; administration never infers.

All service adapters accept fake transports. CLI credentials come only from explicit
process environment, never dotenv, application caches or interactive login files.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import UTC, datetime
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from .cloud_state import HubTransport, PrivateState
from .decision.evidence import (EvidenceClassifier, EvidenceStore, OpenRouterClient,
    MODEL_PRICES_USD_PER_TOKEN, DEFAULT_PROMPT_VERSION, build_graphql_readme_query,
    decode_graphql_readmes, source_from_repo)
from .decision.incremental import IncrementalPlanner, PlanAction, classify_incremental, merge_public_metadata
from .decision.period_budget import RecurringAuthorization, PeriodBudget, approve_authorization
from .decision.source_worker import AioHttpGraphQLTransport
from .decision.store import row_to_repository
from .decision.taxonomy import load_taxonomy
from .operations import DATASET, publish_bundle, update_index, valid_sha, verify_remote, artifact_metadata
from .publication import prepare_classification_publication, prepare_publication, check_freshness, FILES
from .railway_web import bootstrap, DATASET as PUBLIC_URL

MODEL = 'openai/gpt-6-luna'


def checked_prices(prices):
    for field in ('input', 'output'):
        value = prices.get(field)
        if (not isinstance(value, (float, int)) or not math.isfinite(value)
                or value != MODEL_PRICES_USD_PER_TOKEN[MODEL][field]):
            raise ValueError('Reviewed model prices missing or changed; operator review required')


def metadata_identity(path):
    """Content identity excludes labels/operator state and local surrogate history IDs."""
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path)) as conn:
        for table in ('repositories', 'accounts', 'repository_snapshots'):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})') if r[1] != 'id']
            names = ','.join('"' + c + '"' for c in cols)
            digest.update(table.encode())
            for row in conn.execute(f'SELECT {names} FROM {table} ORDER BY {names}'):
                digest.update(json.dumps(row, separators=(',', ':')).encode())
    return digest.hexdigest()


class PublicHub:
    def __init__(self, api, download, *, bootstrap_fn=bootstrap):
        self.api, self.download, self.bootstrap_fn = api, download, bootstrap_fn

    def head(self):
        info = self.api.repo_info(repo_id=DATASET, repo_type='dataset')
        if info.private is not False:
            raise ValueError('Metadata source must be public')
        return valid_sha(info.sha)

    def snapshot(self, target):
        revision = self.head()
        return self.bootstrap_fn(f'{PUBLIC_URL}/resolve/{revision}/data/govtech.db', target)

    # operations uses this adapter for exact remote manifest readback.
    def hf_hub_download(self, **kwargs):
        return self.download(**kwargs)

    def __getattr__(self, name):
        return getattr(self.api, name)


class PriceLookup:
    def __init__(self, *, transport=None):
        self.transport = transport

    async def prices(self):
        if self.transport:
            data = await self.transport()
        else:
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get('https://openrouter.ai/api/v1/models') as response:
                    response.raise_for_status()
                    data = await response.json()
        matches = [m for m in data.get('data', []) if m.get('id') == MODEL]
        if len(matches) != 1:
            raise ValueError('Reviewed model missing from price lookup')
        price = matches[0].get('pricing', {})
        try:
            result = {'input': float(price['prompt']), 'output': float(price['completion'])}
        except (KeyError, ValueError, TypeError):
            raise ValueError('Missing model prices') from None
        checked_prices(result)
        return result


class TargetedSources:
    """One bounded public GraphQL request per selected original; no catalogue selector.

    Missing/inaccessible/oversized responses remain pending. No synthetic README,
    REST fanout, automatic retry or execution of README instructions.
    """
    def __init__(self, token, *, transport=None):
        self.token = token
        self.transport = transport or AioHttpGraphQLTransport()

    async def acquire(self, db, repos, *, limit, deadline, clock=time.monotonic):
        acquired = 0
        for repo in repos[:limit]:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            response = await self.transport.post_graphql(build_graphql_readme_query([repo]),
                token=self.token, timeout=min(30, remaining))
            if response.status in (401, 403):
                raise ValueError('Public README access refused; check GitHub credential')
            if response.status != 200:
                continue
            for item, text, sha, status in decode_graphql_readmes(response.payload, [repo]):
                if status != 'fetched':
                    continue
                EvidenceStore(db).save_source(source_from_repo(item, text, sha, status))
                db.conn.commit()
                acquired += 1
        return acquired


def caveat_exclusions(db):
    """Conservative explicit self-disqualification, never invented negative labels."""
    import re
    pattern = re.compile(r'\b(?:insufficient (?:evidence|support)|not (?:enough|supported|established)|'
                         r'no (?:direct |explicit )?(?:evidence|support)|omit(?:ted)?|cannot (?:infer|establish|confirm))\b', re.I)
    exclusions = []
    rows = db.conn.execute("""SELECT t.*, c.source_hash FROM evidence_classification_tags t
        JOIN evidence_classifications c USING(html_url,taxonomy_version,model)""").fetchall()
    for row in rows:
        source = db.conn.execute('SELECT source_json FROM evidence_source_history WHERE source_hash=?',
                                 (row['source_hash'],)).fetchone()
        if source is None:
            source = db.conn.execute('SELECT source_json FROM evidence_sources WHERE source_hash=?',
                                     (row['source_hash'],)).fetchone()
        fields = json.loads(source[0])['fields'] if source else {}
        reason = None
        if not row['quote'] or row['quote'] not in fields.get(row['source_field'], ''):
            reason = 'Literal quote cannot be verified against recorded source; withdrawn for review'
        elif pattern.search(row['rationale']):
            reason = 'Positive rationale explicitly disclaims support; withdrawn for review'
        if reason:
            exclusions.append({k: row[k] for k in ('html_url','taxonomy_version','model','tag_id')} | {'reason': reason})
    return exclusions


def verify_volume(directory, environ=os.environ, mountinfo=Path('/proc/self/mountinfo')):
    if Path(directory).resolve() != Path('/data') or environ.get('RAILWAY_VOLUME_MOUNT_PATH') != '/data':
        raise ValueError('Paid mode requires explicit Railway /data volume configuration')
    if not any(line.split()[4] == '/data' for line in mountinfo.read_text().splitlines()):
        raise ValueError('Paid mode requires an actual /data mount; ephemeral fallback refused')


class Worker:
    def __init__(self, state, public, *, sources=None, prices=None, client=None,
                 clock=time.monotonic, volume_check=verify_volume):
        self.state, self.public = state, public
        self.sources, self.prices, self.client = sources, prices, client
        self.clock, self.volume_check = clock, volume_check

    def merge(self, db):
        with tempfile.TemporaryDirectory(prefix='govtech-public-') as folder:
            snapshot = self.public.snapshot(Path(folder) / 'public.db')
            merge_public_metadata(db, snapshot.path)
            identity = metadata_identity(self.state.master)
            if identity != self.state.get(db, 'metadata_identity'):
                # Pin once per metadata content, never per classification publication.
                self.state.set(db, 'increment', snapshot.dataset_sha)
                self.state.set(db, 'metadata_identity', identity)
            self.state.set(db, 'source_publication', snapshot.dataset_sha)
            return snapshot.dataset_sha

    async def run(self, *, mode='dry-run', publish=False, rollout=False,
                  inference_enabled=False, authorization_id=None, limit=50, source_limit=50,
                  concurrency=2, deadline_seconds=1800, checkpoint_margin=180):
        if mode not in ('dry-run', 'classify') or not 1 <= concurrency <= 2 or min(limit, source_limit) < 1:
            raise ValueError('Invalid worker bounds')
        if deadline_seconds <= checkpoint_margin or checkpoint_margin < 1:
            raise ValueError('Deadline must reserve checkpoint time')
        if mode == 'dry-run' and (publish or rollout):
            raise ValueError('Dry-run cannot publish or roll out')
        if rollout and not publish:
            raise ValueError('Rollout requires explicit publication')
        started = self.clock()
        paid_deadline = started + deadline_seconds - checkpoint_margin
        status = dict(phase='planned', planned=0, reused=0, acquire=0, classified=0,
                      labelled=0, abstained=0, failed=0, quarantined=0, budget_exhausted=False)
        with self.state.lock():
            db = self.state.open_normal()
            checkpoint_allowed = False
            initial_spend = db.conn.execute('SELECT COALESCE(SUM(COALESCE(actual_usd,reserved_usd)),0) FROM openrouter_budget_ledger').fetchone()[0]
            try:
                if mode == 'classify':
                    if not inference_enabled or not authorization_id:
                        raise ValueError('Classify requires explicit inference enable and authorization ID')
                    self.volume_check(self.state.directory)
                    row = db.conn.execute('SELECT config_json FROM openrouter_recurring_authorization WHERE authorization_id=?',
                                          (authorization_id,)).fetchone()
                    if not row:
                        raise ValueError('Matching persisted authorization required')
                    authorization = RecurringAuthorization(**json.loads(row[0]))
                    if self.state.get(db, 'inference_disabled', True):
                        raise ValueError('Restored state inference-disabled pending operator recovery')
                parent = self.merge(db)
                planner = IncrementalPlanner(db, model=MODEL, prompt_version=DEFAULT_PROMPT_VERSION, taxonomy=load_taxonomy())
                repos = [row_to_repository(row) for row in db.conn.execute(
                    'SELECT * FROM repositories WHERE COALESCE(current,1)=1 AND COALESCE(fork,0)=0 ORDER BY html_url')]
                # Durable rotating cursor prevents failed acquisitions starving later work.
                cursor = self.state.get(db, 'cursor', '')
                repos = [r for r in repos if r.html_url > cursor] + [r for r in repos if r.html_url <= cursor]
                plans = [(r, planner.plan(r)) for r in repos]
                status['planned'] = len(plans)
                status['quarantined'] = sum(p.action == PlanAction.QUARANTINE for _, p in plans)
                acquire = [r for r,p in plans if p.action == PlanAction.ACQUIRE][:source_limit]
                status['acquire'] = len(acquire)
                if mode == 'dry-run':
                    status['reused'] = sum(p.action == PlanAction.REUSE for _,p in plans)
                    return status
                checkpoint_allowed = True
                self.state.set(db, 'terminal_status', status | {'phase': 'running'})
                self.state.checkpoint(db)
                if acquire and self.sources and self.clock() < paid_deadline:
                    try:
                        await asyncio.wait_for(self.sources.acquire(db, acquire, limit=source_limit,
                            deadline=paid_deadline, clock=self.clock), timeout=max(.001, paid_deadline-self.clock()))
                    finally:
                        self.state.set(db, 'cursor', acquire[-1].html_url)
                queue = []
                for repo in repos:
                    plan = planner.plan(repo)
                    if plan.action == PlanAction.REUSE:
                        planner.rebind(repo, plan.source)
                        status['reused'] += 1
                    elif plan.action == PlanAction.CLASSIFY and len(queue) < limit:
                        queue.append(repo)
                budget = PeriodBudget(db, authorization=authorization, increment_id=self.state.get(db, 'increment'), enabled=True)
                if queue and self.clock() < paid_deadline:
                    checked_prices(await asyncio.wait_for(self.prices.prices(), timeout=max(.001, paid_deadline-self.clock())))
                    self.state.mark_dirty(db)
                    classifier = EvidenceClassifier(client=self.client, budget=budget, model=MODEL,
                        taxonomy=load_taxonomy(), prompt_version=DEFAULT_PROMPT_VERSION, max_output_tokens=900)
                    result = await asyncio.wait_for(classify_incremental(db, classifier, queue, concurrency=concurrency),
                                                   timeout=max(.001, paid_deadline-self.clock()))
                    status.update(classified=result.scored, failed=result.failed,
                                  budget_exhausted=bool(result.stopped_early))
                    for repo in queue:
                        row = db.conn.execute('SELECT status FROM evidence_classifications WHERE html_url=? AND model=?',
                                              (repo.html_url, MODEL)).fetchone()
                        if row and row[0] in ('labelled', 'abstained'):
                            status[row[0]] += 1
                    self.state.set(db, 'cursor', queue[-1].html_url)
                month = datetime.now(UTC).strftime('%Y-%m')
                monthly, spent = db.conn.execute('''SELECT
                    COALESCE(SUM(CASE WHEN l.status!='completed' THEN l.reserved_usd
                      WHEN a.authorization_id=? AND a.utc_month=? THEN COALESCE(l.actual_usd,l.reserved_usd) ELSE 0 END),0),
                    COALESCE(SUM(CASE WHEN l.run_id=? THEN COALESCE(l.actual_usd,l.reserved_usd) ELSE 0 END),0)
                    FROM openrouter_budget_ledger l LEFT JOIN openrouter_claim_authorizations a ON a.claim_id=l.id''',
                    (authorization_id, month, budget.run_id)).fetchone()
                total = db.conn.execute('SELECT COALESCE(SUM(COALESCE(actual_usd,reserved_usd)),0) FROM openrouter_budget_ledger').fetchone()[0]
                status.update(month_actual_plus_holds_usd=monthly, increment_spend_usd=spent,
                              run_spend_usd=total-initial_spend)
                if publish:
                    self.publish(db, parent, rollout=rollout)
                status['phase'] = 'budget-exhausted' if status['budget_exhausted'] else 'completed'
                return status
            except BaseException as exc:
                status['phase'] = 'deadline' if isinstance(exc, (asyncio.TimeoutError, asyncio.CancelledError)) else 'failed'
                # Never persist provider exception text, tokens or raw receipt payloads in status.
                status['error'] = type(exc).__name__
                raise
            finally:
                self.state.set(db, 'terminal_status', status)
                try:
                    if checkpoint_allowed:
                        self.state.checkpoint(db)
                finally:
                    db.close()

    def publish(self, db, parent, *, rollout=False, attempts=2):
        previous = self.state.get(db, 'pending_rollout')
        if rollout and previous:
            update_index(previous, self.public)
            self.state.set(db, 'pending_rollout', None)
            return previous
        import uuid
        folder = self.state.directory / 'publication'
        pending = self.state.get(db, 'pending_publication')
        if pending:
            folder = Path(pending['directory'])
            head = self.public.head()
            verify_remote(self.public, head, pending['manifest'], pending['artifacts'])
            receipt = pending | {'dataset_sha': head}
        else:
            for attempt in range(attempts):
                folder = self.state.directory / ('publication-' + uuid.uuid4().hex)
                # Recheck literal source proof and stale metadata via existing exporter.
                for item in caveat_exclusions(db):
                    with db.conn:
                        db.conn.execute('INSERT OR IGNORE INTO evidence_review_exclusions VALUES (?,?,?,?,?)',
                            tuple(item[k] for k in ('html_url','taxonomy_version','model','tag_id','reason')))
                try:
                    manifest = prepare_classification_publication(self.state.master, folder, parent, allow_partial=True)
                except ValueError as exc:
                    if str(exc) not in ('No evidence classifications available for publication', 'No fresh assessed evidence classifications available'):
                        raise
                    manifest = prepare_publication(self.state.master, folder, parent)
                check_freshness(manifest)
                intent = {'directory': str(folder), 'dataset': DATASET, 'parent_commit': parent, 'manifest': manifest,
                    'artifacts': {f'data/{name}': artifact_metadata(folder/name) for name in (*FILES,'manifest.json')}}
                self.state.set(db, 'pending_publication', intent)
                try:
                    receipt = publish_bundle(folder, self.public, parent_commit=parent)
                    break
                except Exception as exc:
                    code = getattr(getattr(exc, 'response', None), 'status_code', None)
                    if code not in (409, 412):
                        # Ambiguous response: exact remote readback or retain intent and stop.
                        head = self.public.head()
                        verify_remote(self.public, head, manifest, intent['artifacts'])
                        receipt = intent | {'dataset_sha': head}
                        break
                    self.state.set(db, 'pending_publication', None)
                    if attempt + 1 == attempts:
                        raise RuntimeError('Public metadata conflict; bounded rebase exhausted') from None
                    parent = self.merge(db)
                    planner = IncrementalPlanner(db, model=MODEL, prompt_version=DEFAULT_PROMPT_VERSION, taxonomy=load_taxonomy())
                    for row in db.conn.execute('SELECT * FROM repositories WHERE current=1 AND fork=0').fetchall():
                        repo = row_to_repository(row)
                        plan = planner.plan(repo)
                        if plan.action == PlanAction.REUSE:
                            planner.rebind(repo, plan.source)
        # Store sufficient receipt for existing HF rollout without re-purchasing results.
        if 'stats' not in receipt:
            from .api.queries import Catalogue
            receipt['stats'] = Catalogue(str(folder / 'govtech.db')).stats()
        self.state.set(db, 'last_publication', receipt)
        self.state.set(db, 'pending_publication', None)
        self.state.set(db, 'pending_rollout', receipt)
        if rollout:
            update_index(receipt, self.public)
            self.state.set(db, 'pending_rollout', None)
        return receipt


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--mode', choices=['dry-run','classify','bootstrap-only','restore-only','approve-recurring'], default='dry-run')
    result.add_argument('--data-dir', default='/data')
    result.add_argument('--publish', action='store_true')
    result.add_argument('--rollout', action='store_true')
    result.add_argument('--authorization-id')
    result.add_argument('--monthly-usd', type=float)
    result.add_argument('--increment-usd', type=float)
    result.add_argument('--approved-at')
    result.add_argument('--effective-at')
    result.add_argument('--limit', type=int, default=50)
    result.add_argument('--source-limit', type=int, default=50)
    result.add_argument('--concurrency', type=int, default=2)
    result.add_argument('--deadline', type=int, default=1800)
    result.add_argument('--checkpoint-margin', type=int, default=180)
    return result


def execute(args):
    from huggingface_hub import HfApi, hf_hub_download
    token = os.environ.get('GOVTECH_HF_TOKEN')
    if not token:
        raise ValueError('Explicit GOVTECH_HF_TOKEN required for private state')
    api = HfApi(token=token)
    def download(**kwargs):
        return hf_hub_download(token=token, **kwargs)
    state = PrivateState(HubTransport(api=api, download=download), args.data_dir)
    if args.mode in ('bootstrap-only', 'restore-only', 'approve-recurring'):
        if args.publish or args.rollout:
            raise ValueError('Administrative modes cannot publish or infer')
        with state.lock():
            if args.mode in ('bootstrap-only', 'restore-only'):
                state.install(restore=args.mode == 'restore-only')
            else:
                if None in (args.authorization_id, args.monthly_usd, args.increment_usd, args.approved_at, args.effective_at):
                    raise ValueError('Approval requires explicit ID, both caps and aware timestamps')
                db = state.open_normal()
                try:
                    approve_authorization(db, RecurringAuthorization(args.authorization_id, args.monthly_usd,
                        args.increment_usd, args.approved_at, args.effective_at))
                    state.checkpoint(db)
                finally:
                    db.close()
        print(json.dumps({'phase': args.mode, 'completed': True}))
        return
    worker = Worker(state, PublicHub(api, download),
        sources=TargetedSources(os.environ.get('GOVTECH_GITHUB_TOKEN', '')),
        prices=PriceLookup(), client=(OpenRouterClient(api_key=os.environ.get('OPENROUTER_API_KEY', ''), model=MODEL)
                                     if args.mode == 'classify' else None))
    status = asyncio.run(worker.run(mode=args.mode, publish=args.publish, rollout=args.rollout,
        inference_enabled=os.environ.get('GOVTECH_INFERENCE_ENABLED') == 'true',
        authorization_id=args.authorization_id, limit=args.limit, source_limit=args.source_limit,
        concurrency=args.concurrency, deadline_seconds=args.deadline, checkpoint_margin=args.checkpoint_margin))
    print(json.dumps(status, sort_keys=True))


def _child(args):
    try:
        execute(args)
    except Exception as exc:
        # Sanitized actionable category; full private ledger/receipts remain on volume.
        print(json.dumps({'phase': 'failed', 'error': type(exc).__name__,
                          'action': 'Inspect private terminal status and checkpoint; do not reset or retry paid claims'}), flush=True)
        raise SystemExit(1)


def main():
    args = parser().parse_args()
    if args.deadline < 1:
        raise SystemExit('Deadline must be positive')
    # Parent watchdog terminates even a blocked synchronous HF SDK or SDK thread.
    process = multiprocessing.get_context('fork').Process(target=_child, args=(args,))
    process.start()
    process.join(args.deadline)
    if process.is_alive():
        process.kill()
        process.join()
        print('{"phase":"hard-deadline","action":"Inspect intact ledger; remote dirty marker remains protective"}')
        raise SystemExit(124)
    raise SystemExit(process.exitcode)


if __name__ == '__main__':
    main()
