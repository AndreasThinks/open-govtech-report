"""Offline worker safety regressions. No credentials or external services."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from govtech_scraper.db import Database
from govtech_scraper.decision.evidence import OpenRouterBudget
from govtech_scraper.cloud_state import PrivateState, HubTransport, validate_master


class FakeHub:
    def __init__(self):
        self.sha = 'a' * 40
        self.private = True
        self.files = {self.sha: {}}
        self.writes = 0
    def repo_info(self, **kwargs):
        return SimpleNamespace(sha=kwargs.get('revision', self.sha), private=self.private)
    def hf_hub_download(self, *, revision, filename, **kwargs):
        return self.files[revision][filename]
    def create_commit(self, *, operations, parent_commit, **kwargs):
        assert parent_commit == self.sha
        self.writes += 1
        new = f'{self.writes:040x}'
        contents = dict(self.files[self.sha])
        for op in operations:
            dest = self.root / f'{new}-{op.path_in_repo.replace("/", "-")}'
            value = op.path_or_fileobj
            dest.write_bytes(value if isinstance(value, bytes) else Path(value).read_bytes())
            contents[op.path_in_repo] = str(dest)
        self.files[new] = contents
        self.sha = new
        return SimpleNamespace(oid=new)


@pytest.fixture
def state(tmp_path):
    api = FakeHub()
    api.root = tmp_path
    return PrivateState(HubTransport(api=api, download=api.hf_hub_download), tmp_path / 'volume'), api


def seed(path):
    db = Database(str(path))
    OpenRouterBudget(db, ceiling_usd=50, run_id='old')
    return db


def test_source_binding_is_atomic_before_post(tmp_path):
    db = seed(tmp_path / 'seed.db')
    budget = OpenRouterBudget(db, ceiling_usd=50, run_id='old')
    claim = budget.reserve(model='openai/gpt-6-luna', request_hash='exact',
        estimated_input_tokens=10, max_output_tokens=900, source_url='https://github.com/a/b')
    assert db.conn.execute('SELECT source_url FROM openrouter_pending_bindings').fetchone()[0] == 'https://github.com/a/b'
    assert db.conn.execute('SELECT status FROM openrouter_budget_ledger WHERE id=?', (claim.id,)).fetchone()[0] == 'reserved'
    with pytest.raises(ValueError, match='binding'):
        budget.reserve_or_pending(model='openai/gpt-6-luna', request_hash='exact',
            estimated_input_tokens=10, max_output_tokens=900, source_url='https://github.com/a/c')
    db.close()


def test_missing_master_never_created(state):
    manager, api = state
    with pytest.raises(ValueError, match='master'):
        manager.open_normal()
    assert not manager.master.exists()
    assert api.writes == 0


def test_reject_empty_ledger(tmp_path):
    db = Database(str(tmp_path / 'empty.db'))
    db.close()
    with pytest.raises(ValueError):
        validate_master(tmp_path / 'empty.db')


def test_private_repo_only(state):
    manager, api = state
    api.private = False
    with pytest.raises(ValueError, match='private'):
        manager.remote()
    with pytest.raises(ValueError):
        HubTransport(api=api, download=api.hf_hub_download, repo='AndreasThinks/government-github-repos')


def test_duplicate_lock(state):
    manager, _ = state
    with manager.lock():
        with pytest.raises(RuntimeError, match='writer'):
            with manager.lock():
                pass


def paths_info(self, *, revision, paths, **kwargs):
    from govtech_scraper.operations import artifact_metadata
    result = []
    for name in paths:
        if name in self.files[revision]:
            meta = artifact_metadata(self.files[revision][name])
            result.append(SimpleNamespace(path=name, size=meta['bytes'], lfs=None, blob_id=meta['git_blob']))
    return result
FakeHub.get_paths_info = paths_info


def upload_seed(manager, api, tmp_path):
    from govtech_scraper.operations import artifact_metadata
    path = tmp_path / 'migration.db'
    db = seed(path)
    db.close()
    meta = artifact_metadata(path)
    manifest = {'schema_version': 1, 'private_state': True, 'state_id': 'f'*32,
        'master': {k: meta[k] for k in ('bytes', 'sha256')},
        'source_publication': 'b'*40, 'metadata_publication': 'b'*40,
        'created_at': '2026-10-14T00:00:00+00:00'}
    m = tmp_path / 'manifest.json'
    m.write_text(json.dumps(manifest))
    r = tmp_path / 'reviews.json'
    r.write_text('[]')
    api.files[api.sha] = {'state/master.db': str(path), 'state/manifest.json': str(m),
                          'state/review-exclusions.json': str(r)}


def test_bootstrap_checkpoint_lost_volume_dirty_restore(state, tmp_path):
    manager, api = state
    upload_seed(manager, api, tmp_path)
    manager.install()
    db = manager.open_normal()
    assert db.conn.execute('PRAGMA synchronous').fetchone()[0] == 2
    manager.checkpoint(db)
    manager.mark_dirty(db)
    # Uncertain POST exists only on volume, beyond last backup.
    OpenRouterBudget(db, ceiling_usd=50, run_id='interrupted').reserve(
        model='openai/gpt-6-luna', request_hash='held', estimated_input_tokens=1,
        max_output_tokens=900, source_url='https://github.com/a/b')
    db.close()
    manager.master.unlink()
    with pytest.raises(ValueError):
        manager.open_normal()
    with pytest.raises(ValueError, match='initialized'):
        manager.install()
    manager.install(restore=True)
    db = manager.open_normal()
    with pytest.raises(ValueError, match='operator recovery'):
        manager.mark_dirty(db)
    manager.checkpoint(db)
    assert manager.remote()[1]['dirty'] is True
    db.close()


def test_bad_hash_preserves_old_target(state, tmp_path):
    manager, api = state
    upload_seed(manager, api, tmp_path)
    path = Path(api.files[api.sha]['state/manifest.json'])
    value = json.loads(path.read_text())
    value['master']['sha256'] = '0'*64
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='manifest'):
        manager.install()
    assert not manager.master.exists()
    assert api.writes == 0


def test_intact_dirty_restart_and_wal_backup(state, tmp_path):
    manager, api = state
    upload_seed(manager, api, tmp_path)
    manager.install()
    db = manager.open_normal()
    manager.mark_dirty(db)
    manager.set(db, 'committed_in_wal', 42)
    db.close()
    db = manager.open_normal()
    manager.mark_dirty(db)
    revision = manager.checkpoint(db)
    import sqlite3
    with sqlite3.connect(api.files[revision]['state/master.db']) as backup:
        assert backup.execute("SELECT value FROM openrouter_worker_state WHERE key='committed_in_wal'").fetchone()[0] == '42'
    assert manager.remote()[1]['dirty'] is False
    db.close()


def test_prices_and_stable_metadata_identity(tmp_path):
    from govtech_scraper.cloud_worker import checked_prices, metadata_identity
    from govtech_scraper.decision.evidence import MODEL_PRICES_USD_PER_TOKEN
    checked_prices({'input': 1e-7, 'output': 5e-7})
    for price in ({}, {'input': float('nan'), 'output': 5e-7}, {'input': 2e-7, 'output': 5e-7}):
        with pytest.raises(ValueError):
            checked_prices(price)
    db = seed(tmp_path / 'identity.db')
    before = metadata_identity(tmp_path / 'identity.db')
    OpenRouterBudget(db, ceiling_usd=50, run_id='different')
    assert metadata_identity(tmp_path / 'identity.db') == before
    db.close()


async def test_targeted_source_transport_limit(tmp_db, sample_repos):
    from govtech_scraper.cloud_worker import TargetedSources
    from govtech_scraper.decision.source_worker import GraphQLResponse
    class Transport:
        calls = 0
        async def post_graphql(self, query, **kwargs):
            self.calls += 1
            return GraphQLResponse(200, {'data': {'r0': {'isPrivate': False,
                'md': {'text': 'Public README', 'oid': 'blob', 'isTruncated': False}}}})
    transport = Transport()
    count = await TargetedSources('fake', transport=transport).acquire(tmp_db, sample_repos,
        limit=1, deadline=100, clock=lambda: 0)
    assert count == 1 and transport.calls == 1


async def test_timeout_and_abrupt_cancel_keep_binding(tmp_db, sample_repo):
    from govtech_scraper.decision.evidence import EvidenceClassifier, source_from_repo
    from govtech_scraper.decision.taxonomy import load_taxonomy
    for error in (TimeoutError, asyncio.CancelledError):
        class Client:
            async def classify(self, *args, **kwargs):
                assert tmp_db.conn.execute('SELECT source_url FROM openrouter_pending_bindings ORDER BY bound_at DESC LIMIT 1').fetchone()[0] == sample_repo.html_url
                raise error()
        source = source_from_repo(sample_repo, error.__name__)
        classifier = EvidenceClassifier(client=Client(), budget=OpenRouterBudget(tmp_db, ceiling_usd=50, run_id='test'),
            model='openai/gpt-6-luna', taxonomy=load_taxonomy())
        with pytest.raises(error):
            await classifier.classify_source(source)
    assert tmp_db.conn.execute("SELECT COUNT(*) FROM openrouter_budget_ledger WHERE status IN ('uncertain','reserved')").fetchone()[0] == 2


@pytest.fixture
def running_worker(state, tmp_path, sample_repos):
    from govtech_scraper.cloud_worker import Worker
    from govtech_scraper.decision.period_budget import RecurringAuthorization, approve_authorization
    manager, api = state
    upload_seed(manager, api, tmp_path)
    manager.install()
    public_path = tmp_path / 'public.db'
    public_db = Database(str(public_path))
    public_db.save_repositories(sample_repos)
    public_db.close()
    class Public:
        def snapshot(self, target):
            return SimpleNamespace(path=public_path, dataset_sha='b'*40)
    db = manager.open_normal()
    approve_authorization(db, RecurringAuthorization('approved', 5, 2, '2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00'))
    db.close()
    return Worker(manager, Public(), volume_check=lambda _: None), api


async def test_default_no_paid_or_remote_writes(running_worker):
    worker, api = running_worker
    writes = api.writes
    status = await worker.run()
    assert status['planned'] == 3
    assert status['acquire'] == 3
    assert api.writes == writes
    db = worker.state.open_normal()
    assert db.conn.execute('SELECT COUNT(*) FROM openrouter_budget_ledger').fetchone()[0] == 0
    db.close()


async def test_runnable_transport_adapters_and_no_prefix_starvation(running_worker):
    from govtech_scraper.cloud_worker import TargetedSources, PriceLookup
    from govtech_scraper.decision.source_worker import GraphQLResponse
    from govtech_scraper.decision.evidence import OpenRouterClient
    worker, api = running_worker
    class GitHub:
        async def post_graphql(self, query, **kwargs):
            return GraphQLResponse(200, {'data': {'r0': {'isPrivate': False,
                'md': {'text': 'A public component.', 'oid': 'sha'}}}})
    class Provider:
        calls = 0
        async def post_json(self, url, *, payload, **kwargs):
            self.calls += 1
            assert payload['model'] == 'openai/gpt-6-luna'
            assert payload['max_tokens'] == 900
            return {'id': 'gen-fake', 'usage': {'cost': .0001}, 'choices': [{'finish_reason': 'stop',
                'message': {'content': json.dumps({'domain': None, 'kind': None, 'tags': [],
                    'abstentions': {'domain': 'insufficient evidence', 'kind': 'insufficient evidence', 'tags': []}})}}]}
    provider = Provider()
    async def prices():
        return {'data': [{'id': 'openai/gpt-6-luna', 'pricing': {'prompt': '0.0000001', 'completion': '0.0000005'}}]}
    worker.sources = TargetedSources('fake', transport=GitHub())
    worker.prices = PriceLookup(transport=prices)
    worker.client = OpenRouterClient(api_key='fake', model='openai/gpt-6-luna', transport=provider)
    for i in range(3):
        status = await worker.run(mode='classify', inference_enabled=True, authorization_id='approved', limit=1, source_limit=1)
        assert status['classified'] == 1 and status['abstained'] == 1
        assert status['run_spend_usd'] == pytest.approx(.0001)
    assert provider.calls == 3
    status = await worker.run(mode='classify', inference_enabled=True, authorization_id='approved', limit=1, source_limit=1)
    assert status['reused'] == 3 and provider.calls == 3
    db = worker.state.open_normal()
    assert len({r[0] for r in db.conn.execute('SELECT run_id FROM openrouter_budget_ledger')}) == 1
    assert not worker.state.remote()[1]['dirty']
    db.close()


async def test_rejected_paid_gate_has_no_remote_writes(running_worker):
    worker, api = running_worker
    writes = api.writes
    with pytest.raises(ValueError, match='enable'):
        await worker.run(mode='classify')
    assert api.writes == writes


def test_publication_repeat_conflict_and_private_strip(running_worker, tmp_path):
    from govtech_scraper.cloud_worker import PublicHub
    from govtech_scraper.decision.evidence import EvidenceStore, source_from_repo, taxonomy_digest, DEFAULT_PROMPT_VERSION
    from govtech_scraper.decision.taxonomy import load_taxonomy
    from govtech_scraper.decision.store import row_to_repository
    worker, _ = running_worker
    api = FakeHub()
    api.root = tmp_path
    api.private = False
    worker.public = PublicHub(api, api.hf_hub_download)
    db = worker.state.open_normal()
    # Use the existing real merge adapter before swapping to publication fake.
    public_db = Database(str(tmp_path / 'public.db'))
    from govtech_scraper.decision.incremental import merge_public_metadata
    merge_public_metadata(db, public_db.db_path)
    public_db.close()
    taxonomy = load_taxonomy()
    for row in db.conn.execute('SELECT * FROM repositories').fetchall():
        repo = row_to_repository(row)
        source = source_from_repo(repo, 'sending emails and SMS')
        store = EvidenceStore(db)
        store.save_source(source)
        store.save_success(html_url=repo.html_url, taxonomy_version=taxonomy.version,
            model='openai/gpt-6-luna', prompt_version=DEFAULT_PROMPT_VERSION,
            input_hash=source.input_hash('openai/gpt-6-luna', DEFAULT_PROMPT_VERSION, taxonomy),
            source=source, domain=None, kind=None, tags=[], usage={'cost': .0001},
            abstentions={'domain': 'unknown', 'kind': 'unknown', 'tags': []}, taxonomy_sha=taxonomy_digest(taxonomy))
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()
    db.conn.execute('INSERT INTO scrape_runs VALUES (?,?,?,?,?,?)', ('run', now, now, 1, 1, 1))
    db.conn.execute('INSERT INTO account_fetches VALUES (?,?,?,?,?,?)', ('run','gov-org','succeeded',3,None,now))
    db.conn.commit()
    first = worker.publish(db, api.sha)
    second = worker.publish(db, api.sha)
    assert first['dataset_sha'] != second['dataset_sha']
    import sqlite3
    with sqlite3.connect(api.files[api.sha]['data/govtech.db']) as conn:
        assert not any(r[0].startswith('openrouter_') for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    assert worker.state.get(db, 'pending_rollout')['dataset_sha'] == second['dataset_sha']
    # A competing metadata publisher wins once. Rebase keeps paid rows and newer history.
    import shutil
    newer = tmp_path / 'newer.db'
    shutil.copyfile(api.files[api.sha]['data/govtech.db'], newer)
    newer_db = Database(str(newer))
    newer_db.conn.execute("UPDATE repositories SET stars=999,last_scraped=?", (datetime.now(UTC).isoformat(),))
    newer_db.conn.commit()
    newer_db.close()
    worker.public.snapshot = lambda target: SimpleNamespace(path=newer, dataset_sha='e'*40)
    original_commit = api.create_commit
    conflicts = []
    def conflict_once(**kwargs):
        if not conflicts:
            conflicts.append(True)
            api.files['e'*40] = dict(api.files[api.sha])
            api.sha = 'e'*40
            error = RuntimeError('conflict')
            error.response = SimpleNamespace(status_code=409)
            raise error
        return original_commit(**kwargs)
    api.create_commit = conflict_once
    original_rows = [tuple(r) for r in db.conn.execute('SELECT * FROM evidence_classifications')]
    worker.publish(db, second['dataset_sha'])
    assert [tuple(r) for r in db.conn.execute('SELECT * FROM evidence_classifications')] == original_rows
    assert db.conn.execute('SELECT MIN(stars) FROM repositories').fetchone()[0] == 999
    db.close()


def test_positive_caveats_and_nonliteral_quotes_are_withdrawn(tmp_db, sample_repo):
    from govtech_scraper.cloud_worker import caveat_exclusions
    from govtech_scraper.decision.evidence import EvidenceStore, source_from_repo, DEFAULT_PROMPT_VERSION, taxonomy_digest
    from govtech_scraper.decision.taxonomy import load_taxonomy
    taxonomy = load_taxonomy()
    source = source_from_repo(sample_repo, 'sending emails and SMS')
    store = EvidenceStore(tmp_db)
    store.save_source(source)
    store.save_success(html_url=sample_repo.html_url, taxonomy_version=taxonomy.version,
        model='openai/gpt-6-luna', prompt_version=DEFAULT_PROMPT_VERSION,
        input_hash=source.input_hash('openai/gpt-6-luna', DEFAULT_PROMPT_VERSION, taxonomy),
        source=source, domain=None, kind=None,
        tags=[{'id':'notifications', 'quote':'sending emails and SMS', 'rationale':'No explicit evidence; omit', 'source_field':'readme'}],
        usage={'cost':.0001}, abstentions={}, taxonomy_sha=taxonomy_digest(taxonomy))
    assert len(caveat_exclusions(tmp_db)) == 1
    tmp_db.conn.execute("UPDATE evidence_classification_tags SET rationale='Supported', quote='invented'")
    assert len(caveat_exclusions(tmp_db)) == 1
    tmp_db.conn.execute("UPDATE evidence_classification_tags SET quote='sending emails and SMS'")
    assert caveat_exclusions(tmp_db) == []


def test_volume_guard(tmp_path):
    from govtech_scraper.cloud_worker import verify_volume
    mount = tmp_path / 'mountinfo'
    mount.write_text('1 2 0:1 / /data rw - ext4 device rw\n')
    verify_volume('/data', {'RAILWAY_VOLUME_MOUNT_PATH':'/data'}, mount)
    for directory, env in [('/tmp', {'RAILWAY_VOLUME_MOUNT_PATH':'/data'}), ('/data', {})]:
        with pytest.raises(ValueError):
            verify_volume(directory, env, mount)
    mount.write_text('1 2 0:1 / / rw - overlay overlay rw\n')
    with pytest.raises(ValueError):
        verify_volume('/data', {'RAILWAY_VOLUME_MOUNT_PATH':'/data'}, mount)


def test_clean_checkpoint_detects_rolled_back_volume(state, tmp_path):
    import shutil
    manager, api = state
    upload_seed(manager, api, tmp_path)
    manager.install()
    old = tmp_path / 'old.db'
    shutil.copyfile(manager.master, old)
    db = manager.open_normal()
    manager.checkpoint(db)
    db.close()
    shutil.copyfile(old, manager.master)
    with pytest.raises(ValueError, match='checkpoint'):
        manager.open_normal()


def test_watchdog_kills_stuck_sync_adapter(monkeypatch):
    import sys
    import time
    from govtech_scraper import cloud_worker
    monkeypatch.setattr(sys, 'argv', ['worker','--deadline','1'])
    monkeypatch.setattr(cloud_worker, 'execute', lambda args: time.sleep(30))
    start = time.monotonic()
    with pytest.raises(SystemExit) as exc:
        cloud_worker.main()
    assert exc.value.code == 124
    assert time.monotonic() - start < 5


def test_restore_cannot_discard_existing_uncheckpointed_holds(state, tmp_path):
    manager, api = state
    upload_seed(manager, api, tmp_path)
    manager.install()
    db = manager.open_normal()
    manager.mark_dirty(db)
    OpenRouterBudget(db, ceiling_usd=50, run_id='hold').reserve(model='openai/gpt-6-luna',
        request_hash='hold', estimated_input_tokens=1, max_output_tokens=900, source_url='url')
    db.close()
    with pytest.raises(ValueError, match='existing master'):
        manager.install(restore=True)
    db = manager.open_normal()
    assert db.conn.execute('SELECT COUNT(*) FROM openrouter_budget_ledger').fetchone()[0] == 1
    db.close()


def test_pending_rollout_resumes_without_republication(running_worker, monkeypatch):
    from govtech_scraper import cloud_worker
    worker, _ = running_worker
    db = worker.state.open_normal()
    receipt = {'dataset_sha': 'c'*40}
    worker.state.set(db, 'pending_rollout', receipt)
    seen = []
    monkeypatch.setattr(cloud_worker, 'update_index', lambda value, api: seen.append(value))
    assert worker.publish(db, 'b'*40, rollout=True) == receipt
    assert seen == [receipt]
    assert worker.state.get(db, 'pending_rollout') is None
    db.close()


def test_private_transfer_rejects_wrong_revision_and_hash(state, tmp_path):
    manager, api = state
    upload_seed(manager, api, tmp_path)
    original = api.repo_info
    api.repo_info = lambda **kwargs: SimpleNamespace(private=True, sha='d'*40)
    with pytest.raises(ValueError, match='revision'):
        manager.hub.read('a'*40, 'state/master.db')
    api.repo_info = original
    real_paths = api.get_paths_info
    def corrupt(**kwargs):
        result = real_paths(**kwargs)
        result[0].blob_id = 'bad'
        return result
    api.get_paths_info = corrupt
    with pytest.raises(ValueError, match='hash'):
        manager.hub.read('a'*40, 'state/master.db')
