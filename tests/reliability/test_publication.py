import csv
import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest


def test_wal_snapshot_and_export_validation(tmp_db, sample_repo, tmp_path):
    from govtech_scraper.publication import prepare_publication, validate_bundle
    tmp_db.save_repositories([sample_repo])
    bundle = tmp_path / 'bundle'
    manifest = prepare_publication(tmp_db.db_path, bundle, 'a' * 40)
    assert manifest['counts']['repositories'] == 1
    assert manifest['source_commit'] == 'a' * 40
    assert manifest['scrape_timestamp'] != manifest['publication_timestamp']
    validate_bundle(bundle)
    with sqlite3.connect(bundle / 'govtech.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM repository_snapshots').fetchone()[0] == 1
    (bundle / 'government_repos_latest.csv').write_text('html_url\nwrong\n')
    with pytest.raises(ValueError):
        validate_bundle(bundle)


def test_missing_export_rejected(tmp_db, sample_repo, tmp_path):
    from govtech_scraper.publication import prepare_publication, validate_bundle
    tmp_db.save_repositories([sample_repo])
    prepare_publication(tmp_db.db_path, tmp_path / 'bundle', 'a' * 40)
    (tmp_path / 'bundle' / 'government_repos_latest.parquet').unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        validate_bundle(tmp_path / 'bundle')


def test_restore_invalid_retains_previous(tmp_db, sample_repo, tmp_path, monkeypatch):
    from govtech_scraper.publication import restore_database
    tmp_db.save_repositories([sample_repo])
    target = tmp_path / 'restore.db'
    with sqlite3.connect(target) as dest:
        tmp_db.conn.backup(dest)
    original = target.read_bytes()
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: io.BytesIO(b'not sqlite'))
    with pytest.raises(RuntimeError):
        restore_database('https://example.test/db', target, attempts=1)
    assert target.read_bytes() == original
    assert not list(tmp_path.glob('*.partial'))


def test_restore_streams_and_validates(tmp_db, sample_repo, tmp_path, monkeypatch):
    from govtech_scraper.publication import restore_database
    tmp_db.save_repositories([sample_repo])
    source = tmp_path / 'source.db'
    with sqlite3.connect(source) as dest:
        tmp_db.conn.backup(dest)
    class Stream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return super().read(size)
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: Stream(source.read_bytes()))
    restore_database('https://example.test/db', tmp_path / 'restored.db', attempts=1)
    with sqlite3.connect(tmp_path / 'restored.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM repositories').fetchone()[0] == 1


def test_freshness_rejects_stale_or_incomplete():
    from govtech_scraper.publication import check_freshness
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    manifest = {'publication_timestamp': now, 'scrape_timestamp': '2020-01-01T00:00:00+00:00',
                'coverage': {'complete': True}, 'export_verified': True}
    with pytest.raises(ValueError):
        check_freshness(manifest)
    manifest['scrape_timestamp'] = now
    manifest['coverage']['complete'] = False
    with pytest.raises(ValueError):
        check_freshness(manifest)


def test_coverage_flag_without_account_evidence_is_not_complete(tmp_db):
    from govtech_scraper.publication import database_metadata
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    tmp_db.conn.execute('INSERT INTO scrape_runs VALUES (?,?,?,?,?,?)', ('fake', now, now, 2, 2, 1))
    tmp_db.conn.commit()
    assert database_metadata(tmp_db.db_path)[2]['complete'] is False


def test_offline_migration_export_dashboard_pipeline(tmp_db, sample_repo, tmp_path, monkeypatch):
    from govtech_scraper.migration import migrate
    from govtech_scraper.publication import prepare_publication
    from streamlit.testing.v1 import AppTest
    import streamlit as st
    st.cache_data.clear()
    tmp_db.save_repositories([sample_repo])
    tmp_db.conn.execute('INSERT INTO repository_tags VALUES (?,?,?,?)', (sample_repo.html_url, 'Node.JS', 1., 'tech-stack'))
    tmp_db.conn.commit()
    migrate(tmp_db)
    bundle = tmp_path / 'integration'
    manifest = prepare_publication(tmp_db.db_path, bundle, 'a' * 40)
    assert manifest['counts']['repository_snapshots'] == 1
    monkeypatch.setenv('GOVTECH_DB_PATH', str(bundle / 'govtech.db'))
    app_file = Path(__file__).resolve().parents[2] / 'web' / 'app.py'
    app = AppTest.from_file(str(app_file), default_timeout=30).run()
    assert not app.exception
    assert app.metric[0].value == '1'
