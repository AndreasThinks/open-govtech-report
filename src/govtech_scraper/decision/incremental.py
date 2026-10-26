"""Local incremental planning, evidence reuse proofs and verified metadata merging.

No network or paid calls. Acquisition and classification are explicit plan actions;
only evidence supplied by the caller or a valid cache can establish reuse.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3

from ..db import Database
from ..models import Repository
from .evidence import (EvidenceClassifier, EvidenceRunSummary, EvidenceSource, EvidenceStore,
                       source_from_repo, taxonomy_digest)
from .taxonomy import Taxonomy

SCHEMA = '''
CREATE TABLE IF NOT EXISTS evidence_source_history (
    source_hash TEXT PRIMARY KEY, source_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_reuse (
    html_url TEXT NOT NULL, taxonomy_version INTEGER NOT NULL, model TEXT NOT NULL,
    input_hash TEXT NOT NULL, metadata_hash TEXT NOT NULL,
    source_hash TEXT NOT NULL, meaningful_hash TEXT NOT NULL, verified_at TEXT NOT NULL,
    PRIMARY KEY(html_url, taxonomy_version, model, input_hash, metadata_hash)
);
CREATE TABLE IF NOT EXISTS evidence_review_exclusions (
    html_url TEXT NOT NULL, taxonomy_version INTEGER NOT NULL, model TEXT NOT NULL,
    tag_id TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY(html_url, taxonomy_version, model, tag_id)
);
'''

# Only observational counters and routine dates are ignored. Every other supplied
# field remains meaningful, including country, fork, archive and branch identity.
VOLATILE_FIELDS = frozenset({'stars', 'forks', 'watchers', 'open_issues', 'size_kb',
                            'commit_count', 'created_at', 'updated_at', 'pushed_at',
                            'last_scraped'})
ACQUIRED = frozenset({'fetched', 'empty', 'missing'})
# A failed classification of unchanged evidence is retried at most once per weekly run.
# Slightly under seven days so a run that starts a little early still picks it up.
RETRY_FAILED_AFTER = timedelta(days=6)


def meaningful_hash(source: EvidenceSource) -> str:
    fields = {k: v for k, v in source.fields.items() if k not in VOLATILE_FIELDS}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class PlanAction(str, Enum):
    ACQUIRE = 'acquire'
    CLASSIFY = 'classify'
    REUSE = 'reuse'
    QUARANTINE = 'quarantine'


@dataclass(frozen=True)
class IncrementalPlan:
    html_url: str
    action: PlanAction
    reason: str
    source: EvidenceSource | None = None
    original_input_hash: str | None = None


def archive_source(conn: sqlite3.Connection, source: EvidenceSource) -> None:
    conn.execute('INSERT OR IGNORE INTO evidence_source_history VALUES (?, ?)',
                 (source.source_hash, json.dumps(asdict(source), sort_keys=True)))


def _cached(row: sqlite3.Row) -> EvidenceSource | None:
    try:
        fields = json.loads(row['source_json'])
        owner, name = fields['repository'].split('/', 1)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return EvidenceSource(row['html_url'], owner, name, fields, row['metadata_hash'],
                          row['source_hash'], row['readme_blob_sha'], row['readme_status'],
                          bool(row['readme_truncated']), row['pushed_at'])


class IncrementalPlanner:
    def __init__(self, db: Database, *, model: str, prompt_version: str, taxonomy: Taxonomy):
        self.db, self.conn = db, db.conn
        self.store = EvidenceStore(db)
        self.model, self.prompt_version, self.taxonomy = model, prompt_version, taxonomy

    def _quarantined(self, url: str) -> bool:
        # An unbound historical POST cannot safely be associated with a source;
        # stop the incremental queue until an operator supplies its exact binding.
        return self.conn.execute('''SELECT 1 FROM openrouter_budget_ledger l
            LEFT JOIN openrouter_response_receipts r ON r.request_hash=l.request_hash AND r.claim_id=l.id
            LEFT JOIN openrouter_pending_bindings b ON b.request_hash=l.request_hash
            WHERE l.status IN ('reserved','uncertain','accounting_breach')
            AND (COALESCE(r.source_url,b.source_url) IS NULL
                 OR COALESCE(r.source_url,b.source_url)=?) LIMIT 1''', (url,)).fetchone() is not None

    def _retry_due(self, classified_at: str | None) -> bool:
        try:
            when = datetime.fromisoformat(str(classified_at).replace('Z', '+00:00'))
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return datetime.now(UTC) - when >= RETRY_FAILED_AFTER

    def plan(self, repo: Repository, source: EvidenceSource | None = None) -> IncrementalPlan:
        def result(action: PlanAction, reason: str, original: str | None = None) -> IncrementalPlan:
            return IncrementalPlan(repo.html_url, action, reason, source, original)
        if self._quarantined(repo.html_url):
            return result(PlanAction.QUARANTINE, 'unresolved or unbound historical claim')
        if source is None:
            cached = self.conn.execute('SELECT * FROM evidence_sources WHERE html_url=?',
                                       (repo.html_url,)).fetchone()
            if cached:
                previous = _cached(cached)
                # A push always requires reacquisition even when metadata identity
                # is otherwise unchanged. Routine metadata refreshes use cached README.
                if (previous is not None and previous.pushed_at == repo.pushed_at and
                    meaningful_hash(source_from_repo(repo, previous.fields.get('readme'))) == meaningful_hash(previous)
                    and previous.readme_status in ACQUIRED):
                    source = source_from_repo(repo, previous.fields.get('readme'),
                                              previous.readme_blob_sha, previous.readme_status)
            if source is None:
                return result(PlanAction.ACQUIRE, 'public README acquisition required')
        if source.html_url != repo.html_url or source.metadata_hash != source_from_repo(repo).metadata_hash:
            raise ValueError('Source does not match current repository metadata')
        if source.readme_status not in ACQUIRED:
            return result(PlanAction.ACQUIRE, 'source acquisition did not establish current evidence')
        row = self.conn.execute('''SELECT * FROM evidence_classifications
            WHERE html_url=? AND taxonomy_version=? AND model=?''',
            (repo.html_url, self.taxonomy.version, self.model)).fetchone()
        if row and row['prompt_version'] == self.prompt_version and row['taxonomy_digest'] == taxonomy_digest(self.taxonomy):
            old = self.conn.execute('SELECT source_json FROM evidence_source_history WHERE source_hash=?',
                                    (row['source_hash'],)).fetchone()
            original = EvidenceSource(**json.loads(old[0])) if old else None
            if original is None:
                cached = self.conn.execute('SELECT * FROM evidence_sources WHERE html_url=? AND source_hash=?',
                                           (repo.html_url, row['source_hash'])).fetchone()
                original = _cached(cached) if cached else None
            if row['status'] == 'failed' and self._retry_due(row['classified_at']):
                return result(PlanAction.CLASSIFY, 'weekly retry of failed classification')
            # Do not reconstruct missing historical evidence or guess equivalence.
            if original and meaningful_hash(original) == meaningful_hash(source):
                for tag in self.conn.execute(
                    'SELECT quote, source_field FROM evidence_classification_tags WHERE html_url=? AND taxonomy_version=? AND model=?',
                    (repo.html_url, self.taxonomy.version, self.model)):
                    if tag['quote'] not in source.fields.get(tag['source_field'], ''):
                        return result(PlanAction.QUARANTINE, 'stored tag quote no longer literal in current evidence')
                return result(PlanAction.REUSE, f"unchanged meaningful evidence; retain {row['status']}", row['input_hash'])
            if row['input_hash'] == source.input_hash(self.model, self.prompt_version, self.taxonomy):
                return result(PlanAction.REUSE, 'exact historical input', row['input_hash'])
            if original is None:
                return result(PlanAction.QUARANTINE, 'historical evidence unavailable; cannot prove a meaningful change')
        return result(PlanAction.CLASSIFY, 'new or changed meaningful evidence or classifier contract')

    def rebind(self, repo: Repository, source: EvidenceSource) -> IncrementalPlan:
        """Record freshness without changing any inference row, tag, cost or date."""
        try:
            self.conn.execute('BEGIN IMMEDIATE')
            plan = self.plan(repo, source)
            if plan.action != PlanAction.REUSE:
                raise ValueError('Freshness can only be rebound after demonstrated equivalence')
            self.conn.execute('''INSERT OR IGNORE INTO evidence_reuse VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (repo.html_url, self.taxonomy.version, self.model, plan.original_input_hash,
                 source.metadata_hash, source.source_hash, meaningful_hash(source), datetime.now(UTC).isoformat()))
            self.store.save_source(source)
            self.conn.commit()
            return plan
        except Exception:
            self.conn.rollback()
            raise


def merge_public_metadata(db: Database, verified_public_path: str | Path) -> None:
    """Merge an already revision/hash-verified public snapshot into an existing master.

    This is NOT bootstrap or remote verification. Only the metadata allowlist is
    copied; inference/ledger/cache/worker state never comes from public data.
    Newer last_scraped wins, history is unioned by values rather than local row IDs.
    Reviewed public exclusions are retained separately from inference provenance.
    """
    from ..publication import validate_database
    validate_database(verified_public_path)
    EvidenceStore(db)
    with closing(sqlite3.connect(Path(verified_public_path).resolve().as_uri() + '?mode=ro', uri=True)) as public:
        public.row_factory = sqlite3.Row
        try:
            db.conn.execute('BEGIN IMMEDIATE')
            tables = {r[0] for r in public.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, keys, stamp in [('repositories', ['html_url'], 'last_scraped'),
                                       ('accounts', ['username'], 'last_seen'),
                                       ('scrape_runs', ['id'], 'finished_at'),
                                       ('account_fetches', ['run_id', 'username'], 'checked_at')]:
                if table not in tables:
                    continue
                cols = [r[1] for r in db.conn.execute(f'PRAGMA table_info({table})')]
                available = {r[1] for r in public.execute(f'PRAGMA table_info({table})')}
                cols = [c for c in cols if c in available]
                names = ','.join(cols)
                updates = ','.join(f'{c}=excluded.{c}' for c in cols if c not in keys)
                newer = f'julianday(excluded.{stamp}) > COALESCE(julianday({table}.{stamp}), -1)'
                if table == 'repositories' and 'current' in cols:
                    # A scrape retires vanished repos (current=0) without touching
                    # their last_scraped, so an equal stamp can still carry news.
                    newer = (f'({newer} OR (julianday(excluded.{stamp}) IS julianday({table}.{stamp})'
                             f' AND excluded.current IS NOT {table}.current))')
                # Forks are copied too: the published catalogue counts them, and
                # classification already excludes them when it plans work.
                for row in public.execute(f'SELECT {names} FROM {table}'):
                    db.conn.execute(f'''INSERT INTO {table} ({names}) VALUES ({','.join('?' for _ in cols)})
                        ON CONFLICT({','.join(keys)}) DO UPDATE SET {updates}
                        WHERE {newer}''', tuple(row))
            for table in ('repository_snapshots',):
                if table not in tables:
                    continue
                # Scrape timestamps span many repositories. Pin the narrow lookup
                # so SQLite cannot choose the timestamp-only index; all values
                # below still participate in NULL-safe history deduplication.
                db.conn.execute('''CREATE INDEX IF NOT EXISTS idx_snapshots_url_timestamp
                    ON repository_snapshots(html_url, scrape_timestamp)''')
                cols = [r[1] for r in db.conn.execute(f'PRAGMA table_info({table})')
                        if not (table == 'repository_snapshots' and r[1] == 'id')]
                names = ','.join(cols)
                match = ' AND '.join(f'{c} IS ?' for c in cols)
                for row in public.execute(f'SELECT {names} FROM {table}'):
                    db.conn.execute(f'''INSERT OR IGNORE INTO {table} ({names})
                        SELECT {','.join('?' for _ in cols)} WHERE NOT EXISTS
                        (SELECT 1 FROM {table} INDEXED BY idx_snapshots_url_timestamp
                         WHERE {match})''', (*row, *row))
            if 'evidence_classifications' in tables:
                for row in public.execute('SELECT * FROM evidence_classifications'):
                    for item in json.loads(row['abstentions_json']).get('publication_review', []):
                        if not item.get('tag_id') or not item.get('reason'):
                            raise ValueError('Malformed reviewed exclusion')
                        db.conn.execute('INSERT OR IGNORE INTO evidence_review_exclusions VALUES (?, ?, ?, ?, ?)',
                            (row['html_url'], row['taxonomy_version'], row['model'], item['tag_id'], item['reason']))
            if 'evidence_review_exclusions' in tables:
                for row in public.execute('SELECT * FROM evidence_review_exclusions'):
                    db.conn.execute('INSERT OR IGNORE INTO evidence_review_exclusions VALUES (?, ?, ?, ?, ?)', tuple(row))
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise


async def classify_incremental(
    db: Database, classifier: EvidenceClassifier, repos: list[Repository], *,
    sources: list[EvidenceSource] | None = None, concurrency: int = 4,
) -> EvidenceRunSummary:
    """Execute a bounded local increment; never acquire sources implicitly.

    Call plan() first to acquire required public sources, then pass them here.
    Missing/invalid acquisitions remain unprocessed; pending claims stay quarantined.
    The caller supplies a PeriodBudget-enabled classifier for recurring paid work.
    """
    from .evidence import EvidenceRunSummary, classify_inputs
    planner = IncrementalPlanner(db, model=classifier.model,
                                 prompt_version=classifier.prompt_version, taxonomy=classifier.taxonomy)
    supplied = {source.html_url: source for source in (sources or [])}
    if len(supplied) != len(sources or []):
        raise ValueError('Duplicate supplied source URLs')
    if len({repo.html_url for repo in repos}) != len(repos):
        raise ValueError('Duplicate repository URLs')
    result = EvidenceRunSummary()
    queue: list[EvidenceSource] = []
    for repo in repos:
        plan = planner.plan(repo, supplied.get(repo.html_url))
        if plan.action == PlanAction.REUSE:
            assert plan.source is not None
            planner.rebind(repo, plan.source)
            result.skipped_unchanged += 1
        elif plan.action == PlanAction.QUARANTINE:
            result.pending += 1
            result.errors.append(f'{repo.html_url}: {plan.reason}')
        elif plan.action == PlanAction.ACQUIRE:
            result.skipped_empty += 1
            result.errors.append(f'{repo.html_url}: {plan.reason}')
        else:
            assert plan.source is not None
            queue.append(plan.source)
    scored = await classify_inputs(db, classifier, queue, concurrency=concurrency)
    for field in ('scored', 'failed', 'pending', 'skipped_unchanged', 'skipped_empty'):
        setattr(result, field, getattr(result, field) + getattr(scored, field))
    result.errors.extend(scored.errors)
    result.stopped_early = scored.stopped_early
    return result


def apply_review_exclusions(conn: sqlite3.Connection) -> None:
    """Apply durable reviews to a publication copy, never to private inference."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'evidence_review_exclusions' not in tables or 'evidence_classifications' not in tables:
        return
    for url, version, model, tag, reason in conn.execute('SELECT * FROM evidence_review_exclusions').fetchall():
        key = (url, version, model)
        row = conn.execute('SELECT abstentions_json FROM evidence_classifications WHERE html_url=? AND taxonomy_version=? AND model=?', key).fetchone()
        if row is None:
            continue
        conn.execute('DELETE FROM evidence_classification_tags WHERE html_url=? AND taxonomy_version=? AND model=? AND tag_id=?', (*key, tag))
        abstentions = json.loads(row[0])
        reviews = abstentions.setdefault('publication_review', [])
        if not any(item['tag_id'] == tag for item in reviews):
            reviews.append({'tag_id': tag, 'reason': reason})
        conn.execute('UPDATE evidence_classifications SET abstentions_json=? WHERE html_url=? AND taxonomy_version=? AND model=?', (json.dumps(abstentions), *key))
        conn.execute('''UPDATE evidence_classifications SET status='abstained'
            WHERE html_url=? AND taxonomy_version=? AND model=? AND status='labelled'
            AND domain IS NULL AND kind IS NULL AND NOT EXISTS (
                SELECT 1 FROM evidence_classification_tags t WHERE t.html_url=? AND t.taxonomy_version=? AND t.model=?)''', (*key, *key))
