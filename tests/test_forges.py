"""Tests for the government GitLab fetcher."""

import asyncio

import pytest

from govtech_scraper.forges import (
    Dedup,
    ForgeHost,
    ForgeNotFound,
    fetch_forge,
    load_forge_hosts,
    main_language,
    parse_forgejo_repo,
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

    def __init__(self, pages, languages=None, licences=None, fail_details=(), parents=None):
        self.pages = pages
        self.parents = parents or {}
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
            return {"license": self.licences.get(pid),
                    "forked_from_project": self.parents.get(pid)}, None
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
    assert (repo.owner, repo.name) == (f"{HOST}/bmi/sub/team", "tool")
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


async def test_fork_parent_comes_from_detail_call_and_survives_reuse(tmp_db):
    a = project("g/a")
    await scrape_forges(tmp_db, [FORGE], get=FakeGitLab(
        [[a]], languages={a["id"]: {"Go": 100}},
        parents={a["id"]: {"web_url": "https://gitlab.com/up/a"}}))
    await scrape_forges(tmp_db, [FORGE], get=FakeGitLab([[dict(a)]]))
    row = tmp_db.conn.execute("SELECT fork, fork_source FROM repositories").fetchone()
    assert tuple(row) == (1, "https://gitlab.com/up/a")


# ---------------------------------------------------------------- Forgejo / Gitea

FJ_HOST = "code.overheid.nl"
FJ_API = f"https://{FJ_HOST}/api/v1"
FJ = ForgeHost(host=FJ_HOST, country="Netherlands", platform="forgejo")


def fj_repo(full_name, host=FJ_HOST, **extra):
    record = {
        "full_name": full_name,
        "html_url": f"https://{host}/{full_name}",
        "description": "omschrijving",
        "language": "Java",
        "stars_count": 2,
        "forks_count": 1,
        "watchers_count": 3,
        "open_issues_count": 0,
        "size": 120,
        "topics": ["overheid"],
        "default_branch": "main",
        "fork": False,
        "parent": None,
        "archived": False,
        "private": False,
        "internal": False,
        "empty": False,
        "mirror": False,
        "original_url": "",
        "created_at": "2026-05-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    record.update(extra)
    return record


class FakeForgejo:
    """Serves /orgs, /users/{o}/repos and /repos/search by page number."""

    def __init__(self, orgs=(), repos=None, missing=()):
        self.orgs = list(orgs)
        self.repos = repos or {}  # owner -> list of repo records
        self.missing = set(missing)
        self.calls = []

    async def __call__(self, url, params):
        self.calls.append((url, params))
        page, limit = params["page"], params["limit"]

        def chunk(items):
            return items[(page - 1) * limit: page * limit]

        if url.endswith("/orgs"):
            return chunk([{"username": o} for o in self.orgs]), None
        if url.endswith("/repos/search"):
            return {"ok": True, "data": chunk([r for v in self.repos.values() for r in v])}, None
        owner = url.split("/users/")[1].split("/")[0]
        if owner in self.missing:
            raise ForgeNotFound(url)
        return chunk(self.repos.get(owner, [])), None


def test_parse_forgejo_maps_fields_and_qualifies_owner():
    repo = parse_forgejo_repo(fj_repo("MinBZK/tool", fork=True,
                                      parent={"html_url": "https://codeberg.org/up/tool"}), FJ)
    assert (repo.owner, repo.name) == (f"{FJ_HOST}/MinBZK", "tool")
    assert (repo.language, repo.size_kb, repo.watchers) == ("Java", 120, 3)
    assert repo.pushed_at == "2026-09-01T00:00:00Z"
    assert repo.fork and repo.fork_source == "https://codeberg.org/up/tool"
    assert repo.license is None


def test_parse_forgejo_skips_private_empty_and_off_host():
    assert parse_forgejo_repo(fj_repo("a/b", private=True), FJ) is None
    assert parse_forgejo_repo(fj_repo("a/b", empty=True), FJ) is None
    assert parse_forgejo_repo(fj_repo("a/b", host="codeberg.org"), FJ) is None


async def test_forgejo_whole_server_reads_orgs_and_pages():
    many = [fj_repo(f"MinBZK/r{i}") for i in range(60)]
    fake = FakeForgejo(orgs=["MinBZK", "Kadaster"],
                       repos={"MinBZK": many, "Kadaster": [fj_repo("Kadaster/k")]})
    result = await fetch_forge(fake, FJ)
    assert len(result.repos) == 61
    assert fake.calls[0][0] == f"{FJ_API}/orgs"
    pages = [p["page"] for u, p in fake.calls if u.endswith("/MinBZK/repos")]
    assert pages == [1, 2]
    assert not any(u.endswith("/languages") for u, _ in fake.calls)  # no per-repo calls


async def test_forgejo_skips_copies_of_indexed_repos():
    fake = FakeForgejo(orgs=["MinBZK"], repos={"MinBZK": [
        fj_repo("MinBZK/mirror", mirror=True, original_url="https://github.com/MinBZK/mirror.git"),
        fj_repo("MinBZK/moved", original_url="https://GitHub.com/MinBZK/moved/"),
        fj_repo("MinBZK/elsewhere", mirror=True, original_url="https://git.example.nl/x/elsewhere"),
        fj_repo("MinBZK/own"),
    ]})
    dedup = Dedup(indexed_urls={"https://github.com/minbzk/mirror", "https://github.com/minbzk/moved"})
    result = await fetch_forge(fake, FJ, dedup=dedup)
    assert sorted(r.name for r in result.repos) == ["elsewhere", "own"]
    assert result.skipped_copies == 2


async def test_submitted_owner_twin_skips_same_named_github_repos():
    forge = ForgeHost(host="codeberg.org", country="France", platform="forgejo",
                      owners=["betagouv"], twins={"betagouv": "betagouv"})
    fake = FakeForgejo(repos={"betagouv": [fj_repo("betagouv/site", host="codeberg.org"),
                                           fj_repo("betagouv/new", host="codeberg.org")]})
    dedup = Dedup(github_names={("betagouv", "site")})
    result = await fetch_forge(fake, forge, dedup=dedup)
    assert [r.name for r in result.repos] == ["new"]
    assert fake.calls[0][0] == "https://codeberg.org/api/v1/users/betagouv/repos"


async def test_missing_submitted_owner_is_skipped_not_fatal():
    forge = ForgeHost(host="codeberg.org", country="France", platform="forgejo",
                      owners=["gone", "here"])
    fake = FakeForgejo(repos={"here": [fj_repo("here/x", host="codeberg.org")]}, missing=["gone"])
    result = await fetch_forge(fake, forge)
    assert [r.name for r in result.repos] == ["x"]
    assert result.missing_owners == ["gone"]


async def test_gitlab_twin_and_user_owner():
    forge = ForgeHost(host="gitlab.com", country="Netherlands", owners=["commonground", "civil"],
                      user_owners={"civil"}, twins={"commonground": "VNG-Realisatie"},
                      owner_countries={"civil": "U.K. Councils"})

    async def get(url, params):
        if "/users/civil/" in url:
            return [project("civil/tool", host="gitlab.com", kind="user")], None
        return [project("commonground/sub/api", host="gitlab.com"),
                project("commonground/new", host="gitlab.com")], None

    dedup = Dedup(github_names={("vng-realisatie", "api")})
    result = await fetch_forge(get, forge, details=False, dedup=dedup)
    by_name = {r.name: r for r in result.repos}
    assert set(by_name) == {"new", "tool"}  # personal kept for a submitted user
    assert by_name["tool"].country == "U.K. Councils"
    assert by_name["new"].country == "Netherlands"
    assert result.skipped_copies == 1


async def test_scrape_dedups_against_github_rows(tmp_db):
    tmp_db.save_repositories([Repository(owner="MinBZK", name="dup", country="Netherlands",
                                         html_url="https://github.com/MinBZK/dup")])
    fake = FakeForgejo(orgs=["MinBZK"], repos={"MinBZK": [
        fj_repo("MinBZK/dup", mirror=True, original_url="https://github.com/MinBZK/dup"),
        fj_repo("MinBZK/only-here")]})
    results = await scrape_forges(tmp_db, [FJ], get=fake)
    assert results[0].skipped_copies == 1
    urls = {r[0] for r in tmp_db.conn.execute("SELECT html_url FROM repositories WHERE current=1")}
    assert urls == {"https://github.com/MinBZK/dup", f"https://{FJ_HOST}/MinBZK/only-here"}


def _write(path, text):
    path.write_text(text)
    return path


def test_submissions_merge_into_hosts(tmp_path):
    hosts = _write(tmp_path / "hosts.yaml", """hosts:
  - host: code.overheid.nl
    platform: forgejo
    country: Netherlands
""")
    subs = tmp_path / "subs"
    subs.mkdir()
    common = """account_type: org
description: "A real body"
evidence: ["https://www.gov.nl/about"]
"""
    _write(subs / "nl-commonground.yaml", "host: gitlab.com\nplatform: gitlab\n"
           "owner: commonground\ncountry: NL\nsame_as_github: VNG-Realisatie\n" + common)
    _write(subs / "fr-betagouv.yaml", "host: codeberg.org\nplatform: forgejo\n"
           "owner: betagouv\ncountry: FR\n" + common)
    _write(subs / "nl-minbzk.yaml", "host: code.overheid.nl\nplatform: forgejo\n"
           "owner: MinBZK\ncountry: NL\nsame_as_github: MinBZK\n" + common)
    _write(subs / "gb-alphagov.yaml", "org: alphagov\ncountry: GB\n" + common)
    _write(subs / "broken.yaml", "host: gitlab.com\n")
    by_host = {h.host: h for h in load_forge_hosts(hosts, submissions_dir=subs)}
    assert set(by_host) == {"code.overheid.nl", "gitlab.com", "codeberg.org"}
    gl = by_host["gitlab.com"]
    assert (gl.platform, gl.owners, gl.country) == ("gitlab", ["commonground"], "Netherlands")
    assert gl.twins == {"commonground": "VNG-Realisatie"}
    assert by_host["codeberg.org"].owner_countries == {"betagouv": "France"}
    # A whole curated server keeps reading every org; the submission only adds its twin.
    assert by_host["code.overheid.nl"].owners == []
    assert by_host["code.overheid.nl"].twins == {"minbzk": "MinBZK"}


def test_shipped_hosts_include_dutch_forgejo():
    hosts = {h.host: h for h in load_forge_hosts()}
    assert hosts["code.overheid.nl"].platform == "forgejo"
    assert hosts["gitlab.opencode.de"].platform == "gitlab"


def test_submit_page_knows_every_curated_server():
    """The Add an account page tells people a curated server is already read in full."""
    import json
    import re
    from pathlib import Path

    js = (Path(__file__).parents[1] / "src/govtech_scraper/site/app.js").read_text()
    servers = json.loads(re.search(r"const FORGE_SERVERS = (\{.*?\});", js).group(1))
    curated = {h.host: h.platform for h in load_forge_hosts() if not h.owners}
    assert servers == curated
