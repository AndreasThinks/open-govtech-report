from unittest.mock import AsyncMock
import pytest
from govtech_scraper import repos
from govtech_scraper.models import GovernmentAccount


class Response:
    def __init__(self, status, data=None, headers=None):
        self.status, self.data, self.headers = status, data, headers or {}
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def json(self):
        return self.data


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


async def test_404_is_recorded_not_transport_failure(tmp_db, sample_account):
    session = Session(Response(404))
    auth = AsyncMock()
    auth.get_headers = lambda token: {}
    assert await repos.fetch_all_repos(session, auth, [sample_account], tmp_db) == []
    assert tmp_db.conn.execute('SELECT status FROM account_fetches').fetchone()[0] == 'not-found'
    assert tmp_db.conn.execute('SELECT complete FROM scrape_runs').fetchone()[0] == 1


async def test_later_page_404_is_failure(tmp_db, sample_account):
    session = Session(Response(200, [], {'Link': '<https://api.github.com/users/gov-org/repos?page=2>; rel="next"'}), Response(404))
    auth = AsyncMock()
    auth.get_headers = lambda token: {}
    with pytest.raises(RuntimeError):
        await repos.fetch_all_repos(session, auth, [sample_account], tmp_db)


async def test_account_limit_is_not_full_coverage(tmp_db, monkeypatch):
    monkeypatch.setattr(repos, 'fetch_repos_for_account', AsyncMock(return_value=[]))
    await repos.fetch_all_repos(None, None, [GovernmentAccount('a', 'GB'), GovernmentAccount('b', 'GB')], tmp_db, limit=1)
    assert tmp_db.conn.execute('SELECT complete FROM scrape_runs').fetchone()[0] == 0
