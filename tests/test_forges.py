"""Tests for the government GitLab fetcher."""

import asyncio

import pytest

from govtech_scraper.forges import (
    ForgeHost,
    fetch_forge,
    load_forge_hosts,
    main_language,
    parse_gitlab_project,
    scrape_forges,
)
from govtech_scraper.models import Repository

HOST = "gitlab.opencode.de"
API = f"https://{HOST}/api/v4"
FORGE = ForgeHost(host=HOST, country="Germany")

_ids = iter(range(1, 10_000))


def project(path, host=HOST, kind="group", **extra):
    record = {
        "id": next(_ids),
        "path_with_namespace": path,
        "web_url": f"https://{host}/{path}",
        "description": "desc",
        "star_count": 3,
        "forks_count": 1,
        "open_issues_count": 4,
        "topics": ["verwaltung"],
        "default_branch": "main",
        "archived": False,
        "created_at": "2024-01-01T00:00:00.000Z",
        "last_activity_at": "2026-01-01T00:00:00.000Z",
        "namespace": {"kind": kind},
    }
    record.update(extra)
    return record


class FakeGitLab:
    """Serves project list pages via next links, plus per-project details."""

    def __init__(self, pages, languages=None, licences=None, fail_details=()):
        self.pages = pages
        self.languages = languages or {}
        self.licences = licences or {}
        self.fail_details = set(fail_details)
        self.calls = []

    async def __call__(self, url, params):
        self.calls.append((url, params))
        if url.endswith("/languages"):
            pid = int(url.split("/")[-2])
            if pid in self.fail_details:
                raise RuntimeError("boom")
            return self.languages.get(pid, {}), None
        if "/projects/" in url and url.rsplit("/", 1)[-1].isdigit():
            pid = int(url.rsplit("/", 1)[-1])
            return {"license": self.licences.get(pid)}, None
        index = int(url.split("#page=")[1]) if "#page=" in url else 0
        nxt = f"{API}/projects#page={index + 1}" if index + 1 < len(self.pages) else None
        return self.pages[index], nxt


def test_shipped_host_list_loads():
    hosts = load_forge_hosts()
    assert {"gitlab.opencode.de", "code.europa.eu"} <= {h.host for h in hosts}
    assert all(h.country for h in hosts)


def test_parse_maps_gitlab_fields():
    repo = parse_gitlab_project(project("bmi/sub/team/tool", forked_from_project={
        "web_url": "https://gitlab.com/up/tool"}), FORGE)
    assert (repo.owner, repo.name) == ("bmi/sub/team", "tool")
    assert repo.country == "Germany"
    assert repo.pushed_at == "2026-01-01T00:00:00.000Z"
    assert repo.fork and repo.fork_source == "https://gitlab.com/up/tool"
    assert repo.topics == ["verwaltung"]


def test_parse_rejects_other_hosts():
    assert parse_gitlab_project(project("a/b", host="gitlab.com"), FORGE) is None


def test_main_language_picks_largest():
    assert main_language({"HTML": 9.6, "Python": 90.4}) == "Python"
    assert main_language({}) is None


async def test_follows_next_links_and_skips_personal_projects():
    fake = FakeGitLab([[project("g/a"), project("someone/x", kind="user")], [project("g/b")]])
    result = await fetch_forge(fake, FORGE, details=False)
    assert [r.name for r in result.repos] == ["a", "b"]
    assert result.skipped_personal == 1
    assert fake.calls[0][1]["visibility"] == "public"
    assert fake.calls[1][1] is None  # the next link carries the params


async def test_include_personal_keeps_user_projects():
    forge = ForgeHost(host=HOST, country="Germany", include_personal=True)
    result = await fetch_forge(FakeGitLab([[project("someone/x", kind="user")]]), forge,
                               details=False)
    assert len(result.repos) == 1


async def test_details_add_language_and_licence_best_effort():
    a, b = project("g/a"), project("g/b")
    fake = FakeGitLab([[a, b]], languages={a["id"]: {"Java": 80, "Shell": 20}},
                      licences={a["id"]: {"key": "eupl-1.2"}}, fail_details=[b["id"]])
    result = await fetch_forge(fake, FORGE)
    by_name = {r.name: r for r in result.repos}
    assert (by_name["a"].language, by_name["a"].license) == ("Java", "eupl-1.2")
    assert by_name["b"].language is None
    assert result.detail_failures == 1


async def test_owner_filter_uses_group_endpoint():
    forge = ForgeHost(host=HOST, country="Germany", owners=["bmi"])
    fake = FakeGitLab([[project("bmi/x")]])
    await fetch_forge(fake, forge, details=False)
    url, params = fake.calls[0]
    assert url == f"{API}/groups/bmi/projects" and params["include_subgroups"] == "true"


async def test_rejects_pagination_to_another_origin():
    async def get(url, params):
        return [project("g/a")], "https://evil.example/api/v4/projects"

    with pytest.raises(RuntimeError, match="origin"):
        await fetch_forge(get, FORGE, details=False)


async def test_scrape_marks_vanished_host_repos_not_current(tmp_db):
    tmp_db.save_repositories([Repository(owner="g", name="r0", html_url="https://github.com/g/r0",
                                         country="Germany")])
    await scrape_forges(tmp_db, [FORGE], details=False,
                        get=FakeGitLab([[project("g/r0"), project("g/r1")]]))
    await scrape_forges(tmp_db, [FORGE], details=False, get=FakeGitLab([[project("g/r1")]]))
    current = dict(tmp_db.conn.execute("SELECT html_url, current FROM repositories").fetchall())
    assert current == {
        "https://github.com/g/r0": 1,  # same owner/name on GitHub is untouched
        f"https://{HOST}/g/r0": 0,
        f"https://{HOST}/g/r1": 1,
    }


async def test_one_failing_host_does_not_stop_others(tmp_db):
    ok = FakeGitLab([[project("g/ok")]])

    async def get(url, params):
        if "code.europa.eu" in url:
            raise RuntimeError("boom")
        return await ok(url, params)

    eu = ForgeHost(host="code.europa.eu", country="European Union")
    results = await scrape_forges(tmp_db, [eu, FORGE], details=False, get=get)
    assert [(r.host, r.status) for r in results] == [
        ("code.europa.eu", "failed"), (HOST, "succeeded")]
    assert tmp_db.get_repo_count() == 1


async def test_limit_stops_early_and_keeps_other_repos_current(tmp_db):
    await scrape_forges(tmp_db, [FORGE], details=False,
                        get=FakeGitLab([[project("g/a"), project("g/b")]]))
    await scrape_forges(tmp_db, [FORGE], limit=1, details=False,
                        get=FakeGitLab([[project("g/a"), project("g/b")]]))
    current = dict(tmp_db.conn.execute("SELECT name, current FROM repositories").fetchall())
    assert current == {"a": 1, "b": 1}


def test_scrape_needs_a_getter(tmp_db):
    with pytest.raises(ValueError):
        asyncio.run(scrape_forges(tmp_db, [FORGE]))


async def test_unchanged_projects_reuse_stored_details(tmp_db):
    a = project("g/a")
    first = FakeGitLab([[a]], languages={a["id"]: {"Go": 100}}, licences={a["id"]: {"key": "mit"}})
    await scrape_forges(tmp_db, [FORGE], get=first)
    second = FakeGitLab([[dict(a)]])
    results = await scrape_forges(tmp_db, [FORGE], get=second)
    assert results[0].details_reused == 1
    assert not any(u.endswith("/languages") for u, _ in second.calls)
    row = tmp_db.conn.execute("SELECT language, license FROM repositories").fetchone()
    assert tuple(row) == ("Go", "mit")


async def test_new_activity_refetches_details(tmp_db):
    a = project("g/a")
    await scrape_forges(tmp_db, [FORGE], get=FakeGitLab(
        [[a]], languages={a["id"]: {"Go": 100}}))
    moved = dict(a, last_activity_at="2026-09-01T00:00:00.000Z")
    second = FakeGitLab([[moved]], languages={a["id"]: {"Rust": 100}})
    await scrape_forges(tmp_db, [FORGE], get=second)
    assert tmp_db.conn.execute("SELECT language FROM repositories").fetchone()[0] == "Rust"
