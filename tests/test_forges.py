"""Tests for the ecosyste.ms forge fetcher."""

import pytest

from govtech_scraper.forges import (
    PER_PAGE,
    ForgeHost,
    fetch_forge_repos,
    load_forge_hosts,
    parse_ecosystems_repo,
    scrape_forges,
)
from govtech_scraper.models import Repository

FORGE = ForgeHost(host="gitlab.opencode.de", country="Germany")


def eco_repo(full_name, host="gitlab.opencode.de", **extra):
    record = {
        "full_name": full_name,
        "html_url": f"https://{host}/{full_name}",
        "description": "desc",
        "language": "Java",
        "stargazers_count": 3,
        "forks_count": 1,
        "subscribers_count": 2,
        "open_issues_count": 4,
        "size": 120,
        "license": "eupl-1.2",
        "topics": ["verwaltung"],
        "default_branch": "main",
        "fork": False,
        "archived": False,
        "created_at": "2024-01-01T00:00:00.000Z",
        "updated_at": "2026-01-01T00:00:00.000Z",
        "pushed_at": "2026-01-01T00:00:00.000Z",
        "mirror_url": None,
        "status": None,
        "source_name": None,
    }
    record.update(extra)
    return record


def pages(*page_items):
    """A fake page fetcher that serves the given pages in order and records calls."""
    calls = []

    async def fetch(url, params):
        calls.append((url, dict(params)))
        index = params["page"] - 1
        return page_items[index] if index < len(page_items) else []

    fetch.calls = calls
    return fetch


def test_shipped_host_list_loads():
    hosts = load_forge_hosts()
    assert {"gitlab.opencode.de", "code.europa.eu"} <= {h.host for h in hosts}
    assert all(h.country for h in hosts)


def test_parse_keeps_nested_group_as_owner():
    repo = parse_ecosystems_repo(eco_repo("bmi/sub/team/tool"), FORGE)
    assert repo.owner == "bmi/sub/team"
    assert repo.name == "tool"
    assert repo.country == "Germany"
    assert repo.watchers == 2 and repo.size_kb == 120
    assert repo.topics == ["verwaltung"]


def test_parse_rejects_other_hosts():
    assert parse_ecosystems_repo(eco_repo("a/b", host="gitlab.com"), FORGE) is None


async def test_paginates_until_short_page():
    full = [eco_repo(f"g/r{i}") for i in range(PER_PAGE)]
    fetch = pages(full, [eco_repo("g/last")])
    result = await fetch_forge_repos(fetch, FORGE)
    assert len(result.repos) == PER_PAGE + 1
    assert [c[1]["page"] for c in fetch.calls] == [1, 2]
    assert fetch.calls[0][0].endswith("/hosts/gitlab.opencode.de/repositories")


async def test_skips_mirrors_and_gone_repos():
    fetch = pages([
        eco_repo("g/keep"),
        eco_repo("g/mirror", mirror_url="https://github.com/up/stream"),
        eco_repo("g/gone", status="not_found"),
    ])
    result = await fetch_forge_repos(fetch, FORGE)
    assert [r.name for r in result.repos] == ["keep"]
    assert result.skipped_mirrors == 1 and result.skipped_gone == 1


async def test_owner_filter_uses_owner_endpoints():
    forge = ForgeHost(host="gitlab.com", country="Netherlands", owners=["minbzk"])
    fetch = pages([eco_repo("minbzk/x", host="gitlab.com")])
    result = await fetch_forge_repos(fetch, forge)
    assert fetch.calls[0][0].endswith("/hosts/gitlab.com/owners/minbzk/repositories")
    assert len(result.repos) == 1


async def test_scrape_marks_vanished_host_repos_not_current(tmp_db):
    github_repo = Repository(owner="g", name="r0", html_url="https://github.com/g/r0",
                             country="Germany")
    tmp_db.save_repositories([github_repo])
    await scrape_forges(tmp_db, [FORGE], fetch_page=pages([eco_repo("g/r0"), eco_repo("g/r1")]))
    await scrape_forges(tmp_db, [FORGE], fetch_page=pages([eco_repo("g/r1")]))
    current = dict(tmp_db.conn.execute("SELECT html_url, current FROM repositories").fetchall())
    assert current == {
        "https://github.com/g/r0": 1,  # same owner/name on GitHub is untouched
        "https://gitlab.opencode.de/g/r0": 0,
        "https://gitlab.opencode.de/g/r1": 1,
    }


async def test_one_failing_host_does_not_stop_others(tmp_db):
    async def fetch(url, params):
        if "code.europa.eu" in url:
            raise RuntimeError("boom")
        return [eco_repo("g/ok")] if params["page"] == 1 else []

    eu = ForgeHost(host="code.europa.eu", country="European Union")
    results = await scrape_forges(tmp_db, [eu, FORGE], fetch_page=fetch)
    assert [(r.host, r.status) for r in results] == [
        ("code.europa.eu", "failed"), ("gitlab.opencode.de", "succeeded")]
    assert tmp_db.get_repo_count() == 1


async def test_limit_stops_early_and_keeps_other_repos_current(tmp_db):
    await scrape_forges(tmp_db, [FORGE], fetch_page=pages([eco_repo("g/a"), eco_repo("g/b")]))
    await scrape_forges(tmp_db, [FORGE], limit=1, fetch_page=pages([eco_repo("g/a")]))
    current = dict(tmp_db.conn.execute("SELECT name, current FROM repositories").fetchall())
    assert current == {"a": 1, "b": 1}


def test_scrape_needs_a_fetcher(tmp_db):
    with pytest.raises(ValueError):
        import asyncio
        asyncio.run(scrape_forges(tmp_db, [FORGE]))
