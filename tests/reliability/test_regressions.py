import asyncio
import csv
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from govtech_scraper.db import Database
from govtech_scraper.models import Repository
from govtech_scraper.export import export_to_csv, export_tag_groups_csv

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_exports_have_headers(tmp_db, tmp_path):
    for exporter, name in [(export_to_csv, 'repos'), (export_tag_groups_csv, 'groups')]:
        target = tmp_path / f'{name}.csv'
        target.write_text('obsolete artifact')
        exporter(tmp_db.db_path, str(target))
        assert target.read_text() != 'obsolete artifact'
        assert list(csv.reader(target.open()))


def test_publish_requires_token(monkeypatch):
    monkeypatch.delenv('HF_TOKEN', raising=False)
    with pytest.raises((RuntimeError, SystemExit)):
        script('publish_hf').main()


def test_restore_fails_closed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = script('download_db')
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(OSError('offline')))
    with pytest.raises((RuntimeError, SystemExit)):
        module.main()
    assert not (tmp_path / 'govtech.db').exists()


async def test_fetch_failure_propagates(tmp_db, sample_account, monkeypatch):
    from govtech_scraper import repos
    monkeypatch.setattr(repos, 'fetch_repos_for_account', AsyncMock(side_effect=OSError('transport')))
    with pytest.raises(RuntimeError):
        await repos.fetch_all_repos(None, None, [sample_account], tmp_db)


def test_tag_cli_errors_nonzero(tmp_db, sample_repo, monkeypatch):
    from govtech_scraper.cli import main
    from govtech_scraper.tagger import BatchResult
    tmp_db.save_repositories([sample_repo])
    monkeypatch.setenv('OPENROUTER_API_KEY', 'offline-test')
    monkeypatch.setattr('govtech_scraper.tagger.tag_batch', AsyncMock(return_value=BatchResult(0, 0, 0, 0, ['credit exhaustion 402'])))
    result = CliRunner().invoke(main, ['--db', tmp_db.db_path, 'tag'])
    assert result.exit_code != 0


def test_tech_stack_has_taxonomy_row(tmp_db, sample_repo):
    tmp_db.save_repositories([sample_repo])
    tmp_db.save_repo_tag(sample_repo.html_url, 'node.js', source='tech-stack')
    assert tmp_db.conn.execute("SELECT usage_count FROM tags WHERE tag='node.js'").fetchone()[0] == 1
    tmp_db.save_repo_tag(sample_repo.html_url, 'node.js', source='tech-stack')
    assert tmp_db.conn.execute("SELECT usage_count FROM tags WHERE tag='node.js'").fetchone()[0] == 1


def test_unknown_commit_count():
    assert Repository('a', 'b', 'url', 'country').commit_count is None


def test_documented_export_cli(tmp_db, sample_repo, tmp_path):
    from govtech_scraper.cli import main
    tmp_db.save_repositories([sample_repo])
    output = tmp_path / 'repos.csv'
    result = CliRunner().invoke(main, ['--db', tmp_db.db_path, 'export', '--format', 'csv', '--output', str(output)])
    assert result.exit_code == 0, result.output
    assert sample_repo.html_url in output.read_text()
