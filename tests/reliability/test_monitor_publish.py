import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock
from datetime import datetime, timezone
import pytest


def script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_monitor_checks_one_revision_and_rejects_missing_artifact(monkeypatch):
    monitor = script('check_freshness')
    now = datetime.now(timezone.utc).isoformat()
    metadata = {'sha': 'immutable-revision', 'lastModified': now, 'siblings': []}
    manifest = {'publication_timestamp': now, 'scrape_timestamp': now,
                'coverage': {'complete': True, 'finished_at': now}, 'export_verified': True}
    reader = Mock(side_effect=[metadata, manifest])
    monkeypatch.setattr(monitor, 'read_json', reader)
    with pytest.raises(ValueError):
        monitor.main([])
    assert '/resolve/immutable-revision/' in reader.call_args_list[1].args[0]


def test_publish_missing_bundle_never_calls_commit(tmp_path, monkeypatch):
    import huggingface_hub
    publish = script('publish_hf')
    monkeypatch.setenv('HF_TOKEN', 'offline-test')
    client = Mock()
    monkeypatch.setattr('huggingface_hub.HfApi', client)
    with pytest.raises(FileNotFoundError):
        publish.main(tmp_path)
    client.assert_not_called()


def test_publication_commit_contains_every_artifact(tmp_db, sample_repo, tmp_path, monkeypatch):
    from govtech_scraper.publication import prepare_publication, FILES
    publish = script('publish_hf')
    now = datetime.now(timezone.utc).isoformat()
    tmp_db.save_repositories([sample_repo])
    tmp_db.conn.execute('INSERT INTO scrape_runs VALUES (?,?,?,?,?,?)', ('fixture', now, now, 1, 1, 1))
    tmp_db.conn.execute('INSERT INTO account_fetches VALUES (?,?,?,?,?,?)', ('fixture', sample_repo.owner, 'succeeded', 1, None, now))
    tmp_db.conn.commit()
    directory = tmp_path / 'bundle'
    prepare_publication(tmp_db.db_path, directory, 'a' * 40)
    monkeypatch.setenv('HF_TOKEN', 'offline-test')
    from tests.reliability.test_index_operations import Hub
    client = Hub(directory)
    monkeypatch.setattr('huggingface_hub.HfApi', Mock(return_value=client))
    (directory / 'baseline.json').write_text('{"dataset_sha": "' + 'b' * 40 + '"}')
    publish.main(directory)
    operations = client.commit['operations']
    assert {op.path_in_repo for op in operations} == {f'data/{name}' for name in (*FILES, 'manifest.json')} | {'README.md'}


def test_monitor_hashes_non_lfs_artifacts(monkeypatch):
    monitor = script('check_freshness')
    from govtech_scraper.publication import FILES
    now = datetime.now(timezone.utc).isoformat()
    manifest = {'publication_timestamp': now, 'scrape_timestamp': now, 'coverage': {'complete': True, 'finished_at': now},
                'export_verified': True, 'files': {name: {'bytes': 3, 'sha256': 'expected'} for name in FILES}}
    metadata = {'sha': 'revision', 'lastModified': now,
                'siblings': [{'rfilename': f'data/{name}', 'size': 3} for name in FILES]}
    monkeypatch.setattr(monitor, 'read_json', Mock(side_effect=[metadata, manifest]))
    monkeypatch.setattr(monitor, 'remote_digest', lambda *a: 'different', raising=False)
    with pytest.raises(ValueError):
        monitor.main([])
