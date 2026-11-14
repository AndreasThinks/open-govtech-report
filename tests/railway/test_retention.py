"""Offline publication-bundle retention regressions. No credentials or external services."""
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from govtech_scraper.cloud_state import PrivateState, HubTransport
from govtech_scraper.operations import DATASET, artifact_metadata
from govtech_scraper.publication import FILES

from test_worker import FakeHub, upload_seed

NAMES = (*FILES, 'manifest.json')


class Remote:
    """Fake public dataset: immutable revision -> independent copies of every artifact."""
    def __init__(self, root):
        self.root, self.files, self.latest, self.verified = root, {}, None, []
    def head(self):
        return self.latest
    def get_paths_info(self, *, revision, paths, **kwargs):
        if revision not in self.files:
            raise RuntimeError('Revision not found')
        self.verified.append(revision)
        result = []
        for name in paths:
            if name in self.files[revision]:
                meta = artifact_metadata(self.files[revision][name])
                result.append(SimpleNamespace(path=name, size=meta['bytes'], lfs=None, blob_id=meta['git_blob']))
        return result
    def hf_hub_download(self, *, revision, filename, **kwargs):
        return self.files[revision][filename]


def bundle_name(index):
    return f'publication-{index:032x}'


def make_bundle(manager, remote, db, index, *, record=True, receipt_file=False, upload=True):
    """A synthetic completed publication: local bundle, exact remote copy and durable record."""
    from govtech_scraper.cloud_retention import record_completed
    folder = manager.directory / bundle_name(index)
    folder.mkdir()
    for name in FILES:
        (folder / name).write_bytes(f'{name}-{index}\n'.encode() * 50)
    manifest = {'publication_timestamp': f'2026-11-{index:02d}T00:00:00+00:00', 'index': index}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    sha = f'{index:040x}'
    artifacts = {f'data/{name}': artifact_metadata(folder / name) for name in NAMES}
    if upload:
        copy = remote.root / f'remote-{sha}'
        shutil.copytree(folder, copy)
        remote.files[sha] = {f'data/{name}': str(copy / name) for name in NAMES}
        remote.latest = sha
    receipt = {'dataset': DATASET, 'dataset_sha': sha, 'parent_commit': 'b' * 40,
               'manifest': manifest, 'artifacts': artifacts, 'stats': {}}
    if receipt_file:
        (folder / 'receipt.json').write_text(json.dumps(receipt))
    if record:
        assert record_completed(manager, db, folder, receipt)
    return folder


def bundle_bytes(folder):
    return sum(path.stat().st_size for path in folder.iterdir())


def states(report):
    return {item['name']: item['state'] for item in report['bundles']}


@pytest.fixture
def volume(tmp_path):
    """Initialized private volume with the worker lock held, as inside Worker.run."""
    api = FakeHub()
    api.root = tmp_path
    manager = PrivateState(HubTransport(api=api, download=api.hf_hub_download), tmp_path / 'volume')
    upload_seed(manager, api, tmp_path)
    manager.install()
    remote = Remote(tmp_path)
    with manager.lock():
        db = manager.open_normal()
        try:
            yield manager, db, remote
        finally:
            db.close()


def test_success_dry_run_inventory_then_verified_deletion_with_receipt(volume):
    from govtech_scraper.cloud_retention import retain
    manager, db, remote = volume
    old, kept = (make_bundle(manager, remote, db, i) for i in (1, 2))
    freed = bundle_bytes(old)
    (manager.directory / 'notes.txt').write_text('operator file')
    (manager.directory / 'publication').mkdir()
    (manager.directory / 'publication' / 'govtech.db').write_text('legacy fixed-name bundle')
    for suffix in ('-wal', '-shm'):
        Path(str(manager.master) + suffix).touch()
    manager.checkpoint(db)

    report = retain(manager, db, remote, keep=1)
    assert report['phase'] == 'dry-run' and report['blockers'] == []
    assert states(report) == {bundle_name(1): 'eligible', bundle_name(2): 'retained-window'}
    assert report['bytes_reclaimable'] == freed and report['bytes_freed'] == 0
    assert report['deleted'] == [] and old.is_dir()
    assert remote.verified == ['0' * 39 + '1']
    assert not (manager.directory / 'retention-receipts.jsonl').exists()
    other = {item['name']: item for item in report['other']}
    assert other['notes.txt']['state'] == 'unknown' and other['publication']['state'] == 'unknown'
    assert {other[n]['state'] for n in ('master.db', 'master.db-wal', 'master.db-shm', 'worker.lock')} == {'protected'}
    assert other['notes.txt']['bytes'] == len('operator file')

    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['phase'] == 'applied' and report['deleted'] == [bundle_name(1)]
    assert report['bytes_freed'] == freed
    assert not old.exists() and kept.is_dir() and bundle_bytes(kept)
    for survivor in ('notes.txt', 'publication/govtech.db', 'master.db', 'master.db-wal', 'master.db-shm', 'worker.lock'):
        assert (manager.directory / survivor).exists()
    lines = (manager.directory / 'retention-receipts.jsonl').read_text().splitlines()
    assert len(lines) == 1
    receipt = json.loads(lines[0])
    assert receipt['name'] == bundle_name(1) and receipt['dataset_sha'] == '0' * 39 + '1'
    assert receipt['bytes_freed'] == freed and set(receipt['files']) == set(NAMES)
    assert receipt['private_checkpoint'] == manager.remote()[1]['checkpoint']
    record = manager.get(db, 'publication_bundles')[bundle_name(1)]
    assert record['status'] == 'deleted' and record['receipt'] == receipt
    # Ledger, evidence, history and review tables are never opened for writing by retention.
    assert db.conn.execute('SELECT COUNT(*) FROM openrouter_budget_config').fetchone()[0] == 1


def test_retention_window_bounds_and_time_budget(volume):
    from govtech_scraper.cloud_retention import retain
    manager, db, remote = volume
    folders = [make_bundle(manager, remote, db, i) for i in (1, 2, 3, 4, 5)]
    manager.checkpoint(db)
    for bad in (0, -1, 11, True, 2.5, None):
        with pytest.raises(ValueError, match='window'):
            retain(manager, db, remote, keep=bad, apply=True)
    assert all(f.is_dir() for f in folders)
    # An exhausted time budget defers instead of risking the wrapper's hard deadline.
    report = retain(manager, db, remote, keep=2, apply=True, clock=lambda: 10, deadline=10)
    assert report['deleted'] == [] and states(report)[bundle_name(1)] == 'deferred'
    # Per-run bound: oldest first.
    report = retain(manager, db, remote, keep=2, apply=True, max_delete=1)
    assert report['deleted'] == [bundle_name(1)]
    assert states(report)[bundle_name(2)] == 'deferred'
    report = retain(manager, db, remote, keep=2, apply=True)
    assert report['deleted'] == [bundle_name(2), bundle_name(3)]
    assert [f.exists() for f in folders] == [False, False, False, True, True]
    # A larger window never deletes what a smaller one kept.
    assert retain(manager, db, remote, keep=10, apply=True)['deleted'] == []


def test_dirty_marker_and_missing_clean_checkpoint_preserve_everything(volume):
    from govtech_scraper.cloud_retention import retain
    manager, db, remote = volume
    # A volume that never checkpointed has no clean private checkpoint at all.
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['phase'] == 'blocked' and any('checkpoint' in reason for reason in report['blockers'])
    manager.checkpoint(db)
    folders = [make_bundle(manager, remote, db, i) for i in (1, 2)]
    # Completion is recorded locally but no private checkpoint carries it yet.
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['deleted'] == [] and states(report)[bundle_name(1)] == 'awaiting-checkpoint'
    manager.checkpoint(db)
    manager.mark_dirty(db)
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['phase'] == 'blocked' and report['deleted'] == []
    assert any('dirty' in reason for reason in report['blockers'])
    assert remote.verified == [] and all(f.is_dir() for f in folders)
    # A failed checkpoint leaves the remote marker pointing at an older checkpoint identity.
    manager.checkpoint(db)
    manager.set(db, 'checkpoint_id', 'rolled-forward-locally-only')
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['phase'] == 'blocked' and any('checkpoint' in reason for reason in report['blockers'])
    # Restored dirty lineage: inference-disabled state is unresolved operator recovery.
    manager.checkpoint(db)
    manager.set(db, 'inference_disabled', True)
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['phase'] == 'blocked' and all(f.is_dir() for f in folders)
    manager.set(db, 'inference_disabled', False)
    assert retain(manager, db, remote, keep=1, apply=True)['deleted'] == [bundle_name(1)]


def test_pending_and_ambiguous_uploads_are_preserved(volume):
    from govtech_scraper.cloud_retention import retain
    manager, db, remote = volume
    completed = [make_bundle(manager, remote, db, i) for i in (1, 2, 3)]
    # Interrupted or conflict-abandoned attempt: bundle exists, nothing proves completion.
    unknown = make_bundle(manager, remote, db, 4, record=False, upload=False)
    # Ambiguous upload: durable intent, no verified receipt.
    uncertain = make_bundle(manager, remote, db, 5, record=False, upload=False)
    manager.set(db, 'pending_publication', {'directory': str(uncertain), 'manifest': {}, 'artifacts': {}})
    manager.checkpoint(db)
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['phase'] == 'blocked' and report['deleted'] == []
    assert any('pending publication' in reason for reason in report['blockers'])
    assert states(report)[bundle_name(5)] == 'pending-publication'
    assert all(f.is_dir() for f in (*completed, unknown, uncertain))
    # Resolved elsewhere; the unproven bundles still stay, and a pending rollout pins its bundle.
    manager.set(db, 'pending_publication', None)
    manager.set(db, 'pending_rollout', {'dataset_sha': f'{1:040x}'})
    manager.set(db, 'last_publication', {'dataset_sha': f'{2:040x}'})
    manager.checkpoint(db)
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['deleted'] == []
    assert states(report) == {bundle_name(1): 'pending-rollout', bundle_name(2): 'last-publication',
        bundle_name(3): 'retained-window', bundle_name(4): 'unrecorded', bundle_name(5): 'unrecorded'}
    assert all(f.is_dir() for f in (*completed, unknown, uncertain))
    manager.set(db, 'pending_rollout', None)
    manager.set(db, 'last_publication', {'dataset_sha': f'{3:040x}'})
    manager.checkpoint(db)
    assert retain(manager, db, remote, keep=1, apply=True)['deleted'] == [bundle_name(1), bundle_name(2)]
    assert unknown.is_dir() and uncertain.is_dir()


@pytest.mark.parametrize('damage', ['remote-bytes', 'remote-missing-revision', 'remote-missing-file',
                                    'remote-manifest', 'remote-error', 'local-bytes', 'local-missing-file',
                                    'local-extra-file', 'record-sha'])
def test_verification_failure_preserves_bundle(volume, damage):
    from govtech_scraper.cloud_retention import retain
    manager, db, remote = volume
    old = make_bundle(manager, remote, db, 1)
    make_bundle(manager, remote, db, 2)
    manager.checkpoint(db)
    sha = f'{1:040x}'
    if damage == 'remote-bytes':
        Path(remote.files[sha]['data/govtech.db']).write_bytes(b'different remote artifact')
    elif damage == 'remote-missing-revision':
        del remote.files[sha]
    elif damage == 'remote-missing-file':
        del remote.files[sha]['data/tag_groups.csv']
    elif damage == 'remote-manifest':
        # Same bytes length and listing metadata, different downloaded manifest content.
        fake = remote.root / 'other-manifest.json'
        fake.write_text('{"publication_timestamp": "tampered"}')
        remote.hf_hub_download = lambda **kwargs: str(fake)
    elif damage == 'remote-error':
        def offline(**kwargs):
            raise OSError('network down')
        remote.get_paths_info = offline
    elif damage == 'local-bytes':
        (old / 'govtech.db').write_bytes(b'locally modified after publication')
    elif damage == 'local-missing-file':
        (old / 'tag_groups.csv').unlink()
    elif damage == 'local-extra-file':
        (old / 'operator-notes.txt').write_text('unknown file')
    elif damage == 'record-sha':
        records = manager.get(db, 'publication_bundles')
        records[bundle_name(1)]['dataset_sha'] = 'main'
        manager.set(db, 'publication_bundles', records)
    before = sorted(p.name for p in old.iterdir())
    for apply in (False, True):
        report = retain(manager, db, remote, keep=1, apply=apply)
        assert report['deleted'] == [] and report['bytes_freed'] == 0
        item = next(i for i in report['bundles'] if i['name'] == bundle_name(1))
        assert item['state'] in ('verification-failed', 'unknown-contents') and item['reason']
    assert sorted(p.name for p in old.iterdir()) == before
    assert manager.get(db, 'publication_bundles')[bundle_name(1)]['status'] == 'completed'
    assert not (manager.directory / 'retention-receipts.jsonl').exists()


def test_concurrency_requires_the_worker_lock(tmp_path):
    from govtech_scraper.cloud_retention import retain, run_retention
    api = FakeHub()
    api.root = tmp_path
    manager = PrivateState(HubTransport(api=api, download=api.hf_hub_download), tmp_path / 'volume')
    upload_seed(manager, api, tmp_path)
    manager.install()
    remote = Remote(tmp_path)
    with manager.lock():
        db = manager.open_normal()
        old = make_bundle(manager, remote, db, 1)
        make_bundle(manager, remote, db, 2)
        manager.checkpoint(db)
        # A second process (the operator command) cannot run beside the cron worker.
        other = PrivateState(manager.hub, manager.directory)
        with pytest.raises(RuntimeError, match='writer'):
            run_retention(other, remote, keep=1, apply=True)
        with pytest.raises(RuntimeError, match='lock'):
            retain(other, db, remote, keep=1, apply=True)
        assert old.is_dir()
        db.close()
    # Outside the lock the library entry point refuses; it never takes the lock itself,
    # so the worker can call it while already holding the lock without deadlock.
    db = manager.open_normal()
    try:
        with pytest.raises(RuntimeError, match='lock'):
            retain(manager, db, remote, keep=1, apply=True)
    finally:
        db.close()
    assert old.is_dir()
    # The operator command takes the lock itself and releases it.
    report = run_retention(manager, remote, keep=1, apply=True)
    assert report['deleted'] == [bundle_name(1)] and not old.exists()
    with manager.lock():
        pass


def test_symlinks_and_path_escapes_are_rejected(volume, tmp_path):
    from govtech_scraper.cloud_retention import record_completed, retain
    manager, db, remote = volume
    outside = tmp_path / 'outside'
    outside.mkdir()
    # 1: bundle directory swapped for a symlink to an identical tree outside the volume.
    one = make_bundle(manager, remote, db, 1)
    shutil.copytree(one, outside / 'tree')
    shutil.rmtree(one)
    one.symlink_to(outside / 'tree', target_is_directory=True)
    # 2: an artifact swapped for a symlink to an identical file outside the volume.
    two = make_bundle(manager, remote, db, 2)
    shutil.copyfile(two / 'govtech.db', outside / 'govtech.db')
    (two / 'govtech.db').unlink()
    (two / 'govtech.db').symlink_to(outside / 'govtech.db')
    # 3: nested directory inside a bundle.
    three = make_bundle(manager, remote, db, 3)
    (three / 'nested').mkdir()
    (three / 'nested' / 'file').write_text('x')
    # 4: hard link shared with a file outside the bundle.
    four = make_bundle(manager, remote, db, 4)
    os.link(four / 'tag_groups.csv', outside / 'hardlink.csv')
    make_bundle(manager, remote, db, 5)
    # Records can never name anything except a direct publication-<uuid> child.
    victim = outside / 'publication-' / 'x'
    victim.parent.mkdir()
    victim.write_text('outside')
    receipt = {'dataset_sha': 'c' * 40, 'manifest': {'publication_timestamp': '2026-11-01T00:00:00+00:00'},
               'artifacts': {f'data/{name}': {'bytes': 1, 'sha256': '0' * 64, 'git_blob': '0' * 40} for name in NAMES}}
    for folder in (outside / 'tree', manager.directory / 'publication', manager.directory,
                   manager.directory / bundle_name(6) / '..' / '..' / 'outside',
                   manager.directory / 'nested' / bundle_name(6), Path(bundle_name(6))):
        assert record_completed(manager, db, folder, receipt) is False
    records = manager.get(db, 'publication_bundles')
    template = records[bundle_name(5)]
    for name in ('../outside/tree', str(outside / 'tree'), 'publication', 'publication-' + 'G' * 32,
                 bundle_name(7) + '/../../outside/tree', '.', ''):
        records[name] = dict(template, published_at='2020-01-01T00:00:00+00:00')
    manager.set(db, 'publication_bundles', records)
    manager.checkpoint(db)
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['deleted'] == [] and report['bytes_freed'] == 0
    assert states(report) == {bundle_name(1): 'rejected-symlink', bundle_name(2): 'unknown-contents',
        bundle_name(3): 'unknown-contents', bundle_name(4): 'unknown-contents', bundle_name(5): 'retained-window'}
    assert len(report['rejected_records']) == 7
    assert sorted(p.name for p in (outside / 'tree').iterdir()) == sorted(NAMES)
    assert (outside / 'govtech.db').exists() and (outside / 'hardlink.csv').exists() and victim.exists()
    assert one.is_symlink() and (two / 'govtech.db').is_symlink() and (three / 'nested' / 'file').exists()
    # The receipt log itself must not be redirected through a symlink.
    make_bundle(manager, remote, db, 8)
    manager.checkpoint(db)
    (manager.directory / 'retention-receipts.jsonl').symlink_to(outside / 'redirected.jsonl')
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['phase'] == 'blocked' and report['deleted'] == []
    assert not (outside / 'redirected.jsonl').exists() and (manager.directory / bundle_name(5)).is_dir()


def test_idempotent_repeat_and_interrupted_deletion_resume(volume, monkeypatch):
    from govtech_scraper import cloud_retention
    from govtech_scraper.cloud_retention import retain
    manager, db, remote = volume
    one, two, three = (make_bundle(manager, remote, db, i) for i in (1, 2, 3))
    manager.checkpoint(db)
    freed = bundle_bytes(one)
    # Crash after the first unlink: durable intent, partially removed bundle.
    real_unlink, calls = os.unlink, []
    def crash(name, **kwargs):
        if calls:
            raise KeyboardInterrupt('killed by watchdog')
        calls.append(name)
        return real_unlink(name, **kwargs)
    monkeypatch.setattr(cloud_retention.os, 'unlink', crash)
    with pytest.raises(KeyboardInterrupt):
        retain(manager, db, remote, keep=1, apply=True)
    monkeypatch.setattr(cloud_retention.os, 'unlink', real_unlink)
    assert one.is_dir() and len(list(one.iterdir())) == len(NAMES) - 1
    assert manager.get(db, 'publication_bundles')[bundle_name(1)]['status'] == 'deleting'
    assert two.is_dir() and three.is_dir()
    # Dry-run reports the resumable deletion without touching it.
    report = retain(manager, db, remote, keep=1)
    assert states(report)[bundle_name(1)] == 'deleting' and one.is_dir()
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['deleted'] == [bundle_name(1), bundle_name(2)]
    assert not one.exists() and not two.exists() and three.is_dir()
    lines = [json.loads(line) for line in (manager.directory / 'retention-receipts.jsonl').read_text().splitlines()]
    assert [line['name'] for line in lines] == [bundle_name(1), bundle_name(2)]
    assert lines[0]['bytes_freed'] == freed and lines[0]['resumed'] is True
    # Repeats change nothing and write nothing.
    snapshot = manager.get(db, 'publication_bundles')
    for _ in range(2):
        report = retain(manager, db, remote, keep=1, apply=True)
        assert report['deleted'] == [] and report['bytes_freed'] == 0
        assert states(report) == {bundle_name(3): 'retained-window'}
    assert manager.get(db, 'publication_bundles') == snapshot
    assert len((manager.directory / 'retention-receipts.jsonl').read_text().splitlines()) == 2
    # A volume that lost a recorded bundle is reported, never treated as an error or re-deleted.
    shutil.rmtree(three)
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['deleted'] == [] and report['missing'] == [bundle_name(3)]


def test_reconcile_old_bundles_requires_receipt_proof(volume):
    from govtech_scraper.cloud_retention import reconcile_receipts, retain
    manager, db, remote = volume
    proven = make_bundle(manager, remote, db, 1, record=False, receipt_file=True)
    no_receipt = make_bundle(manager, remote, db, 2, record=False)
    wrong_remote = make_bundle(manager, remote, db, 3, record=False, receipt_file=True)
    Path(remote.files[f'{3:040x}']['data/govtech.db']).write_bytes(b'not what was published')
    modified = make_bundle(manager, remote, db, 4, record=False, receipt_file=True)
    (modified / 'govtech.db').write_bytes(b'changed after receipt')
    newest = make_bundle(manager, remote, db, 5)
    manager.checkpoint(db)
    report = reconcile_receipts(manager, db, remote)
    assert report['adopted'] == [] and report['adoptable'] == [bundle_name(1)]
    assert manager.get(db, 'publication_bundles').keys() == {bundle_name(5)}
    report = reconcile_receipts(manager, db, remote, apply=True)
    assert report['adopted'] == [bundle_name(1)]
    assert {item['name'] for item in report['unproven']} == {bundle_name(2), bundle_name(3), bundle_name(4)}
    assert all(item['reason'] for item in report['unproven'])
    assert manager.get(db, 'publication_bundles')[bundle_name(1)]['source'] == 'receipt'
    # Adoption alone is not deletion authority: it must first reach a clean private checkpoint.
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['deleted'] == [] and states(report)[bundle_name(1)] == 'awaiting-checkpoint'
    manager.checkpoint(db)
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['deleted'] == [bundle_name(1)]
    assert not proven.exists()
    assert all(f.is_dir() for f in (no_receipt, wrong_remote, modified, newest))
    assert reconcile_receipts(manager, db, remote, apply=True)['adopted'] == []


def test_operator_command_dry_run_reconcile_and_apply(tmp_path):
    from govtech_scraper.cloud_retention import run_retention
    api = FakeHub()
    api.root = tmp_path
    manager = PrivateState(HubTransport(api=api, download=api.hf_hub_download), tmp_path / 'volume')
    upload_seed(manager, api, tmp_path)
    manager.install()
    remote = Remote(tmp_path)
    with manager.lock():
        db = manager.open_normal()
        legacy = make_bundle(manager, remote, db, 1, record=False, receipt_file=True)
        make_bundle(manager, remote, db, 2)
        manager.checkpoint(db)
        db.close()
    writes = api.writes
    report = run_retention(manager, remote, keep=1, reconcile=True)
    assert report['phase'] == 'dry-run' and report['reconcile']['adoptable'] == [bundle_name(1)]
    assert api.writes == writes and legacy.is_dir()
    report = run_retention(manager, remote, keep=1, apply=True, reconcile=True)
    assert report['reconcile']['adopted'] == [bundle_name(1)] and report['deleted'] == [bundle_name(1)]
    assert not legacy.exists() and api.writes > writes
    # The deletion receipt reached the private checkpoint and the lineage is still clean.
    assert not manager.remote()[1]['dirty']
    db = manager.open_normal()
    assert manager.get(db, 'publication_bundles')[bundle_name(1)]['status'] == 'deleted'
    # A dirty lineage blocks the operator command before any remote write.
    manager.mark_dirty(db)
    db.close()
    writes = api.writes
    report = run_retention(manager, remote, keep=1, apply=True, reconcile=True)
    assert report['phase'] == 'blocked' and api.writes == writes


def test_cli_retention_mode_is_dry_run_by_default(monkeypatch):
    from govtech_scraper import cloud_worker
    args = cloud_worker.parser().parse_args(['--mode', 'retention'])
    assert args.apply is False and args.reconcile_receipts is False and args.retention_keep == 3
    assert cloud_worker.parser().parse_args([]).no_auto_retention is False
    monkeypatch.setenv('GOVTECH_HF_TOKEN', 'fake-never-used')
    for argv in (['--mode', 'dry-run', '--apply'], ['--mode', 'classify', '--reconcile-receipts'],
                 ['--mode', 'retention', '--publish'], ['--mode', 'retention', '--retention-keep', '0']):
        with pytest.raises(ValueError):
            cloud_worker.execute(cloud_worker.parser().parse_args(argv))


@pytest.fixture
def publishing_worker(tmp_path, sample_repos, monkeypatch):
    """Real Worker.run/publish with fake bundle preparation and fake public commits."""
    from govtech_scraper import cloud_worker
    from govtech_scraper.db import Database
    from govtech_scraper.decision.period_budget import RecurringAuthorization, approve_authorization
    api = FakeHub()
    api.root = tmp_path
    manager = PrivateState(HubTransport(api=api, download=api.hf_hub_download), tmp_path / 'volume')
    upload_seed(manager, api, tmp_path)
    manager.install()
    public_path = tmp_path / 'public.db'
    public_db = Database(str(public_path))
    public_db.save_repositories(sample_repos)
    public_db.close()
    remote = Remote(tmp_path)
    remote.snapshot = lambda target: SimpleNamespace(path=public_path, dataset_sha='b' * 40)
    db = manager.open_normal()
    approve_authorization(db, RecurringAuthorization('approved', 5, 2, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'))
    db.close()
    counter = []
    def prepare(master, folder, *, source_commit, allow_partial):
        counter.append(folder)
        folder.mkdir()
        for name in FILES:
            (folder / name).write_bytes(f'{name}-{len(counter)}\n'.encode() * 50)
        manifest = {'publication_timestamp': f'2026-11-{len(counter):02d}T00:00:00+00:00', 'source_commit': source_commit}
        (folder / 'manifest.json').write_text(json.dumps(manifest))
        return manifest
    def publish_bundle(folder, public, *, parent_commit):
        sha = f'{len(counter):040x}'
        copy = tmp_path / f'remote-{sha}'
        shutil.copytree(folder, copy)
        remote.files[sha] = {f'data/{name}': str(copy / name) for name in NAMES}
        remote.latest = sha
        manifest = json.loads((folder / 'manifest.json').read_text())
        receipt = {'dataset': DATASET, 'dataset_sha': sha, 'parent_commit': parent_commit, 'manifest': manifest,
                   'artifacts': {f'data/{name}': artifact_metadata(folder / name) for name in NAMES}, 'stats': {}}
        (folder / 'receipt.json').write_text(json.dumps(receipt))
        return receipt
    monkeypatch.setattr(cloud_worker, 'prepare_classification_publication', prepare)
    monkeypatch.setattr(cloud_worker, 'check_freshness', lambda manifest: None)
    monkeypatch.setattr(cloud_worker, 'publish_bundle', publish_bundle)
    def build(**kwargs):
        return cloud_worker.Worker(manager, remote, volume_check=lambda _: None, source_commit='c' * 40, **kwargs)
    return build, manager, remote, counter


RUN = dict(mode='classify', publish=True, inference_enabled=True, authorization_id='approved')


async def test_worker_run_prunes_automatically_after_checkpoint(publishing_worker):
    build, manager, remote, folders = publishing_worker
    worker = build(retention_keep=2)
    for expected_alive in ([0], [0, 1], [1, 2], [2, 3]):
        status = await worker.run(**RUN)
        assert status['phase'] == 'completed' and status['retention']['phase'] == 'applied'
        assert [i for i, f in enumerate(folders) if f.exists()] == expected_alive
    assert status['retention']['deleted'] == [folders[1].name]
    assert status['retention']['bytes_freed'] > 0
    # Every deleted bundle was re-verified at its own immutable revision first.
    assert remote.verified.count(f'{1:040x}') >= 1 and remote.verified.count(f'{2:040x}') >= 1
    # Volume state stays valid for the next run and the terminal status carries the summary.
    db = manager.open_normal()
    try:
        assert manager.get(db, 'terminal_status')['retention']['deleted'] == [folders[1].name]
        assert not manager.remote()[1]['dirty']
    finally:
        db.close()
    lines = (manager.directory / 'retention-receipts.jsonl').read_text().splitlines()
    assert [json.loads(line)['name'] for line in lines] == [folders[0].name, folders[1].name]


async def test_worker_dry_run_and_disabled_retention_delete_nothing(publishing_worker):
    build, manager, remote, folders = publishing_worker
    disabled = build(retention_keep=None)
    for _ in range(3):
        status = await disabled.run(**RUN)
        assert 'retention' not in status
    assert all(f.is_dir() for f in folders) and len(folders) == 3
    status = await build(retention_keep=1).run()
    assert 'retention' not in status and all(f.is_dir() for f in folders)
    status = await build(retention_keep=1).run(**RUN)
    assert len(status['retention']['deleted']) == 3


async def test_cleanup_failure_never_masks_job_result(publishing_worker, monkeypatch):
    from govtech_scraper import cloud_worker
    build, manager, remote, folders = publishing_worker
    worker = build(retention_keep=1)
    await worker.run(**RUN)
    def broken(*args, **kwargs):
        raise OSError('disk error with /secret/path')
    monkeypatch.setattr(cloud_worker, 'retain', broken)
    status = await worker.run(**RUN)
    assert status['phase'] == 'completed'
    assert status['retention'] == {'phase': 'failed', 'error': 'OSError'}
    assert all(f.is_dir() for f in folders)
    # A failed primary job keeps its own exception and never starts cleanup.
    monkeypatch.setattr(cloud_worker, 'retain', lambda *a, **k: pytest.fail('cleanup after failed job'))
    def refuse(folder, public, *, parent_commit):
        raise ValueError('Remote manifest mismatch')
    monkeypatch.setattr(cloud_worker, 'publish_bundle', refuse)
    remote.head = lambda: 'f' * 40
    with pytest.raises(Exception) as failure:
        await worker.run(**RUN)
    assert not isinstance(failure.value, OSError)
    db = manager.open_normal()
    try:
        status = manager.get(db, 'terminal_status')
        assert status['phase'] == 'failed' and 'retention' not in status
        # The ambiguous attempt stays pending and pins every bundle.
        assert manager.get(db, 'pending_publication')['directory'] == str(folders[-1])
    finally:
        db.close()
    assert all(f.is_dir() for f in folders)


def test_wrapper_fails_fast_beside_running_worker_and_never_deadlocks(tmp_path, monkeypatch, capfd):
    """Real main() watchdog and forked child: the wrapper holds no lock, the child fails fast."""
    import sys
    import time
    from govtech_scraper import cloud_worker
    from govtech_scraper.cloud_retention import run_retention
    api = FakeHub()
    api.root = tmp_path
    manager = PrivateState(HubTransport(api=api, download=api.hf_hub_download), tmp_path / 'volume')
    upload_seed(manager, api, tmp_path)
    manager.install()
    remote = Remote(tmp_path)
    def execute(args):
        state = PrivateState(manager.hub, args.data_dir)
        print(json.dumps(run_retention(state, remote, keep=args.retention_keep, apply=args.apply)))
    monkeypatch.setattr(cloud_worker, 'execute', execute)
    monkeypatch.setattr(sys, 'argv', ['worker', '--mode', 'retention', '--apply', '--retention-keep', '1',
                                      '--data-dir', str(manager.directory), '--deadline', '30'])
    with manager.lock():
        db = manager.open_normal()
        old = make_bundle(manager, remote, db, 1)
        make_bundle(manager, remote, db, 2)
        manager.checkpoint(db)
        db.close()
        start = time.monotonic()
        with pytest.raises(SystemExit) as exc:
            cloud_worker.main()
        assert exc.value.code == 1 and time.monotonic() - start < 10
        assert 'Another worker writer holds the volume lock' in capfd.readouterr().out
        assert old.is_dir()
    with pytest.raises(SystemExit) as exc:
        cloud_worker.main()
    assert exc.value.code == 0
    assert json.loads(capfd.readouterr().out)['deleted'] == [bundle_name(1)]
    assert not old.exists() and (manager.directory / bundle_name(2)).is_dir()


def test_snapshot_sidecars_left_by_readers_do_not_pin_bundles(volume):
    """SQLite readers leave govtech.db-shm and an empty -wal; only an empty log is disposable."""
    from govtech_scraper.cloud_retention import retain
    manager, db, remote = volume
    one, two = (make_bundle(manager, remote, db, i) for i in (1, 2))
    make_bundle(manager, remote, db, 3)
    for folder in (one, two):
        (folder / 'govtech.db-shm').write_bytes(b'\0' * 32768)
        (folder / 'govtech.db-wal').touch()
    # Frames never checkpointed into the published main file are local-only data.
    (two / 'govtech.db-wal').write_bytes(b'uncheckpointed frames')
    manager.checkpoint(db)
    freed = bundle_bytes(one)
    report = retain(manager, db, remote, keep=1, apply=True)
    assert report['deleted'] == [bundle_name(1)] and report['bytes_freed'] == freed
    assert not one.exists()
    assert states(report)[bundle_name(2)] == 'unknown-contents'
    assert (two / 'govtech.db-wal').read_bytes() == b'uncheckpointed frames'
