import sqlite3
from unittest.mock import AsyncMock
import pytest
from govtech_scraper.models import Repository


async def test_account_outcomes_and_failed_coverage(tmp_db, sample_account, monkeypatch):
    from govtech_scraper import repos
    monkeypatch.setattr(repos, 'fetch_repos_for_account', AsyncMock(side_effect=TimeoutError('secret value')))
    with pytest.raises(RuntimeError):
        await repos.fetch_all_repos(None, None, [sample_account], tmp_db)
    run = tmp_db.conn.execute('SELECT * FROM scrape_runs').fetchone()
    assert run['complete'] == 0
    outcome = tmp_db.conn.execute('SELECT * FROM account_fetches').fetchone()
    assert outcome['status'] == 'failed'
    assert 'secret value' not in str(dict(outcome))


async def test_successful_empty_enumeration_preserves_history(tmp_db, sample_repo, sample_account, monkeypatch):
    from govtech_scraper import repos
    tmp_db.save_repositories([sample_repo])
    monkeypatch.setattr(repos, 'fetch_repos_for_account', AsyncMock(return_value=[]))
    await repos.fetch_all_repos(None, None, [sample_account], tmp_db)
    assert tmp_db.get_repo_count() == 1
    assert tmp_db.conn.execute('SELECT current FROM repositories').fetchone()[0] == 0
    assert tmp_db.conn.execute('SELECT COUNT(*) FROM repository_snapshots').fetchone()[0] == 1


def test_migration_idempotent_preserves_links_and_snapshots(tmp_db, sample_repo):
    from govtech_scraper.migration import migrate
    tmp_db.save_repositories([sample_repo])
    tmp_db.conn.executemany('INSERT INTO repository_tags VALUES (?,?,?,?)',
                           [(sample_repo.html_url, 'Node.JS', 1, 'tech-stack'),
                            (sample_repo.html_url, 'python', 1, 'tech-stack')])
    tmp_db.conn.commit()
    snapshots = [tuple(r) for r in tmp_db.conn.execute('SELECT * FROM repository_snapshots')]
    migrate(tmp_db)
    first = '\n'.join(tmp_db.conn.iterdump())
    migrate(tmp_db)
    assert '\n'.join(tmp_db.conn.iterdump()) == first
    assert [tuple(r) for r in tmp_db.conn.execute('SELECT * FROM repository_snapshots')] == snapshots
    assert tmp_db.conn.execute('SELECT COUNT(*) FROM repository_tags').fetchone()[0] == 2
    assert tmp_db.conn.execute('SELECT COUNT(*) FROM repository_tags rt LEFT JOIN tags t ON t.tag=rt.tag WHERE t.tag IS NULL').fetchone()[0] == 0
    assert tmp_db.conn.execute("SELECT kind FROM tags WHERE tag='python'").fetchone()[0] == 'language'
    assert tmp_db.conn.execute("SELECT tag FROM repository_tags WHERE tag='node.js'").fetchone()
    assert tmp_db.conn.execute('SELECT COUNT(*) FROM tag_label_history').fetchone()[0] == 1
