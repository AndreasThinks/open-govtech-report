"""Repositories on government code servers outside GitHub: GitLab and Forgejo/Gitea.

Each server's own REST API is read anonymously: public projects need no token.
Hosts come from forge_hosts.yaml (whole government servers, curated) plus accepted
community submissions of single accounts on shared servers such as gitlab.com or
codeberg.org (see submissions.py).

GitLab: the project list carries last activity, archived state, stars, topics and
whether the owner is a group or a person; language and licence take one extra
call each per project, so they are optional.

Forgejo/Gitea (Codeberg, code.overheid.nl): the repo list already carries language,
fork parent and mirror origin, so it needs no per-repo calls. It has no licence.

Repos are stored with a host-qualified owner ("gitlab.com/commonground"), so their
owner/name never collides with a GitHub account, whose logins cannot contain dots.

Copies of repos already in the index are skipped: Forgejo says where a mirror or
migrated repo came from, and a submission can name the same body's GitHub account
(`same_as_github`) so repos with the same name there are skipped too.

Projects in personal namespaces are skipped by default: on shared servers such
as code.europa.eu or code.ornl.gov they are mostly individual experiments.

An earlier version used ecosyste.ms, which indexes these servers but leaves
language, activity and archived state empty for them (tested 2026-10-15).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import aiohttp
import yaml
from aiolimiter import AsyncLimiter

from .db import Database
from .models import Repository
from .retry import retry_async

logger = logging.getLogger(__name__)

FORGE_HOSTS_PATH = Path(__file__).parent / "forge_hosts.yaml"
USER_AGENT = "open-govtech-index (+https://govtechindex.cloud)"
PER_PAGE = 100
MAX_PAGES = 2000

# Public servers run by small teams, and several (OpenCoDE, code.europa.eu) throttle
# anonymous callers hard: stay slow, and pause the whole host on a 429.
REQUESTS_PER_SECOND = 2
DETAIL_CONCURRENCY = 2
MAX_PAUSE_SECONDS = 300


PLATFORMS = ("gitlab", "forgejo")
FORGEJO_PAGE = 50  # Forgejo's default maximum page size


class ForgeNotFound(Exception):
    """A 404: not retried, since the account or server path is gone."""


@dataclass
class ForgeHost:
    """A government code server, or the accounts read from a shared one."""
    host: str
    country: str
    level: str = "national"
    description: str = ""
    platform: str = "gitlab"
    # Top-level groups (or Forgejo owners) to limit a shared server (e.g. gitlab.com) to.
    owners: list[str] = field(default_factory=list)
    include_personal: bool = False
    # Owners that are people rather than groups (submitted with account_type: user).
    user_owners: set[str] = field(default_factory=set)
    # Per-owner country label, for shared servers holding several governments' accounts.
    owner_countries: dict[str, str] = field(default_factory=dict)
    # Owner -> GitHub login of the same body: repos with the same name there are copies.
    twins: dict[str, str] = field(default_factory=dict)

    def owner_key(self, namespace: str) -> str | None:
        """The configured owner a namespace path sits under (longest match), if any."""
        ns = namespace.lower()
        best = None
        for owner in self.owners:
            o = owner.lower()
            if (ns == o or ns.startswith(o + "/")) and (best is None or len(o) > len(best)):
                best = o
        return best

    def country_for(self, namespace: str) -> str:
        key = self.owner_key(namespace)
        return self.owner_countries.get(key, self.country) if key else self.country


@dataclass
class Dedup:
    """What is already in the index, to skip copies of it."""
    indexed_urls: set[str] = field(default_factory=set)  # normalised html_urls on other hosts
    github_names: set[tuple[str, str]] = field(default_factory=set)  # (login, name), lower case


def normalise_url(url: str | None) -> str:
    url = (url or "").strip().lower().rstrip("/")
    return url.removesuffix(".git").rstrip("/")


@dataclass
class HostResult:
    host: str
    status: str  # "succeeded" or "failed"
    repos: list[Repository] = field(default_factory=list)
    skipped_personal: int = 0
    skipped_copies: int = 0
    missing_owners: list[str] = field(default_factory=list)
    detail_failures: int = 0
    details_reused: int = 0
    error_type: str | None = None


# get(url, params) -> (decoded JSON, next page URL or None)
Getter = Callable[[str, dict | None], Awaitable[tuple[object, str | None]]]


def load_forge_hosts(path: Path = FORGE_HOSTS_PATH,
                     submissions_dir: Path | None = None) -> list[ForgeHost]:
    """Curated hosts, plus accepted submissions on other forges merged in by host.

    Pass submissions_dir=None to read only the curated list.
    """
    data = yaml.safe_load(path.read_text())
    hosts: dict[str, ForgeHost] = {}
    for entry in data.get("hosts", []):
        platform = str(entry.get("platform", "gitlab")).strip().lower()
        if platform not in PLATFORMS:
            raise ValueError(f"Unknown platform {platform!r} for {entry['host']}")
        forge = ForgeHost(
            host=str(entry["host"]).strip().lower(),
            country=str(entry["country"]).strip(),
            level=str(entry.get("level", "national")),
            description=str(entry.get("description", "")),
            platform=platform,
            owners=[str(o).strip() for o in entry.get("owners") or []],
            include_personal=bool(entry.get("include_personal", False)),
        )
        hosts[forge.host] = forge
    if submissions_dir is not None:
        from .submissions import load_submissions

        def warn(p, errors):
            logger.warning("Skipping invalid submission %s: %s", p.name, "; ".join(errors))

        for sub in load_submissions(submissions_dir, on_invalid=warn):
            if not sub.is_github:
                _merge_submission(hosts, sub)
    return list(hosts.values())


def _merge_submission(hosts: dict[str, ForgeHost], sub) -> None:
    forge = hosts.get(sub.host)
    if forge is None:
        forge = hosts[sub.host] = ForgeHost(host=sub.host, country=sub.country, level="submitted",
                                            description="Community-submitted accounts",
                                            platform=sub.platform)
    elif forge.platform != sub.platform:
        logger.warning("Submission %s says %s is %s, but it is read as %s; skipping",
                       sub.file, sub.host, sub.platform, forge.platform)
        return
    key = sub.owner.lower()
    if sub.same_as_github:
        forge.twins[key] = sub.same_as_github
    if forge.owners or forge.level == "submitted":
        # A whole curated server already covers every group; only add to owner lists.
        if key not in {o.lower() for o in forge.owners}:
            forge.owners.append(sub.owner)
        forge.owner_countries[key] = sub.country
        if sub.account_type == "user":
            forge.user_owners.add(key)


def parse_gitlab_project(data: dict, forge: ForgeHost) -> Repository | None:
    """Map a GitLab project record onto Repository.

    Returns None for records whose URL isn't on the forge (defensive).
    """
    html_url = (data.get("web_url") or "").rstrip("/")
    full_name = data.get("path_with_namespace") or ""
    if not html_url.startswith(f"https://{forge.host}/") or "/" not in full_name:
        return None
    # GitLab groups nest: keep the whole group path as the owner, behind the host.
    namespace, name = full_name.rsplit("/", 1)
    parent = data.get("forked_from_project") or {}
    last_activity = data.get("last_activity_at")
    return Repository(
        owner=f"{forge.host}/{namespace}",
        name=name,
        html_url=html_url,
        country=forge.country_for(namespace),
        description=data.get("description") or None,
        stars=data.get("star_count") or 0,
        forks=data.get("forks_count") or 0,
        open_issues=data.get("open_issues_count") or 0,
        topics=list(data.get("topics") or data.get("tag_list") or []),
        default_branch=data.get("default_branch") or "main",
        fork=bool(parent),
        archived=bool(data.get("archived")),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at") or last_activity,
        # GitLab has no push timestamp; last activity is the closest equivalent.
        pushed_at=last_activity,
        # Full URL: a bare path could collide with an unrelated GitHub repo.
        fork_source=parent.get("web_url"),
    )


def parse_forgejo_repo(data: dict, forge: ForgeHost) -> Repository | None:
    """Map a Forgejo/Gitea repository record onto Repository.

    Returns None for private, internal, empty or off-host records.
    """
    html_url = (data.get("html_url") or "").rstrip("/")
    full_name = data.get("full_name") or ""
    if (not html_url.startswith(f"https://{forge.host}/") or "/" not in full_name
            or data.get("private") or data.get("internal") or data.get("empty")):
        return None
    namespace, name = full_name.split("/", 1)
    parent = data.get("parent") or {}
    # Forgejo has no push timestamp; updated_at moves on every push.
    updated = data.get("updated_at")
    return Repository(
        owner=f"{forge.host}/{namespace}",
        name=name,
        html_url=html_url,
        country=forge.country_for(namespace),
        description=data.get("description") or None,
        language=data.get("language") or None,
        stars=data.get("stars_count") or 0,
        forks=data.get("forks_count") or 0,
        watchers=data.get("watchers_count") or 0,
        open_issues=data.get("open_issues_count") or 0,
        size_kb=data.get("size") or 0,
        topics=list(data.get("topics") or []),
        default_branch=data.get("default_branch") or "main",
        fork=bool(data.get("fork")),
        archived=bool(data.get("archived")),
        created_at=data.get("created_at"),
        updated_at=updated,
        pushed_at=updated,
        fork_source=parent.get("html_url") or None,
    )


def main_language(languages: object) -> str | None:
    """GitLab returns {"Python": 90.4, "HTML": 9.6}; keep the largest."""
    if not isinstance(languages, dict) or not languages:
        return None
    return max(languages.items(), key=lambda kv: kv[1])[0]


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def _header_seconds(value: str | None, default: float) -> float:
    try:
        return max(0.0, min(MAX_PAUSE_SECONDS, float(value)))
    except (TypeError, ValueError):
        return default


def make_getter(session: aiohttp.ClientSession) -> Getter:
    limiters: dict[str, AsyncLimiter] = {}
    paused_until: dict[str, float] = {}

    # ForgeNotFound is deliberately not retried.
    @retry_async(max_attempts=6, base_delay=5.0,
                 retry_on=(aiohttp.ClientError, RuntimeError, asyncio.TimeoutError))
    async def get(url: str, params: dict | None) -> tuple[object, str | None]:
        host = url.split("/")[2]
        loop = asyncio.get_running_loop()
        # One 429 pauses every request to that host, not just the one that hit it.
        while (wait := paused_until.get(host, 0) - loop.time()) > 0:
            await asyncio.sleep(wait)
        limiter = limiters.setdefault(host, AsyncLimiter(REQUESTS_PER_SECOND, 1))
        async with limiter, session.get(url, params=params,
                                        headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status == 429:
                wait = _header_seconds(resp.headers.get("Retry-After"), 60)
                paused_until[host] = max(paused_until.get(host, 0), loop.time() + wait)
                logger.warning("%s rate limited; pausing it for %ss", host, int(wait))
                raise RuntimeError("Forge 429")
            if resp.status >= 500:
                # Overloaded small servers answer 502/503: back off the whole host too.
                paused_until[host] = max(paused_until.get(host, 0), loop.time() + 30)
                logger.warning("%s answered %s; pausing it for 30s", host, resp.status)
                raise RuntimeError(f"Forge error {resp.status}")
            if resp.status == 404:
                raise ForgeNotFound(url)
            if resp.status != 200:
                raise RuntimeError(f"Forge error {resp.status}")
            if resp.headers.get("RateLimit-Remaining") == "0":
                # GitLab sends RateLimit-Reset as a Unix timestamp.
                try:
                    wait = float(resp.headers.get("RateLimit-Reset", "")) - time.time()
                except ValueError:
                    wait = 60
                paused_until[host] = loop.time() + max(0.0, min(MAX_PAUSE_SECONDS, wait))
            return await resp.json(), _next_link(resp.headers.get("Link", ""))
    return get


def _is_copy(repo: Repository, original_url: str | None, forge: ForgeHost,
             dedup: Dedup | None) -> bool:
    """A mirror or migration of a repo already indexed, or a twin of the same body's GitHub repo."""
    if dedup is None:
        return False
    if original_url and normalise_url(original_url) in dedup.indexed_urls:
        return True
    namespace = repo.owner.split("/", 1)[1]
    key = forge.owner_key(namespace) or namespace.lower()
    twin = forge.twins.get(key)
    return bool(twin) and (twin.lower(), repo.name.lower()) in dedup.github_names


async def _list_projects(get: Getter, forge: ForgeHost, limit: int | None,
                         result: HostResult,
                         dedup: Dedup | None = None) -> list[tuple[int, Repository]]:
    api = f"https://{forge.host}/api/v4"
    params = {"per_page": PER_PAGE, "pagination": "keyset", "order_by": "id", "sort": "asc",
              "visibility": "public"}
    if forge.owners:
        starts = []
        for o in forge.owners:
            if o.lower() in forge.user_owners:
                starts.append((o, f"{api}/users/{quote(o, safe='')}/projects", dict(params)))
            else:
                starts.append((o, f"{api}/groups/{quote(o, safe='')}/projects",
                               {**params, "include_subgroups": "true"}))
    else:
        starts = [(None, f"{api}/projects", params)]

    found: list[tuple[int, Repository]] = []
    seen: set[str] = set()
    for owner, url, first_params in starts:
        next_url: str | None = url
        page_params: dict | None = first_params
        for _ in range(MAX_PAGES):
            if next_url is None:
                break
            try:
                items, next_url = await get(next_url, page_params)
            except ForgeNotFound:
                if owner is None:
                    raise
                # A submitted account that no longer exists: skip it, keep the others.
                logger.warning("%s/%s not found; skipping it", forge.host, owner)
                result.missing_owners.append(owner)
                break
            page_params = None  # the next link carries them
            if not isinstance(items, list):
                raise RuntimeError("Invalid GitLab project list")
            if next_url and not next_url.startswith(f"{api}/"):
                raise RuntimeError("Invalid pagination origin")
            for item in items:
                personal = (item.get("namespace") or {}).get("kind") == "user"
                if personal and not forge.include_personal and not (
                        owner and owner.lower() in forge.user_owners):
                    result.skipped_personal += 1
                    continue
                repo = parse_gitlab_project(item, forge)
                if repo is None or repo.html_url in seen:
                    continue
                if _is_copy(repo, None, forge, dedup):
                    result.skipped_copies += 1
                    continue
                seen.add(repo.html_url)
                found.append((item["id"], repo))
                if limit is not None and len(found) >= limit:
                    return found
        else:
            raise RuntimeError(f"Pagination exceeded {MAX_PAGES} pages for {forge.host}")
    return found


async def _forgejo_pages(get: Getter, url: str):
    """Yield each page of a Forgejo list endpoint, by page number."""
    for page in range(1, MAX_PAGES + 1):
        items, _ = await get(url, {"limit": FORGEJO_PAGE, "page": page})
        if isinstance(items, dict):  # /repos/search wraps its list
            items = items.get("data")
        if not isinstance(items, list):
            raise RuntimeError("Invalid Forgejo list")
        yield items
        if len(items) < FORGEJO_PAGE:
            return
    raise RuntimeError(f"Pagination exceeded {MAX_PAGES} pages for {url}")


async def _list_forgejo(get: Getter, forge: ForgeHost, limit: int | None,
                        result: HostResult, dedup: Dedup | None = None) -> list[Repository]:
    api = f"https://{forge.host}/api/v1"
    if forge.owners:
        owners: list[str] = list(forge.owners)
    elif forge.include_personal:
        owners = []
    else:
        # A whole server: read organisations, so personal accounts are left out.
        owners = []
        async for page in _forgejo_pages(get, f"{api}/orgs"):
            owners += [o["username"] for o in page if isinstance(o, dict) and o.get("username")]
    starts = ([(o, f"{api}/users/{quote(o, safe='')}/repos") for o in owners]
              if owners or not forge.include_personal else [(None, f"{api}/repos/search")])

    found: list[Repository] = []
    seen: set[str] = set()
    for owner, url in starts:
        try:
            async for items in _forgejo_pages(get, url):
                for item in items:
                    repo = parse_forgejo_repo(item, forge)
                    if repo is None or repo.html_url in seen:
                        continue
                    if _is_copy(repo, item.get("original_url"), forge, dedup):
                        result.skipped_copies += 1
                        continue
                    seen.add(repo.html_url)
                    found.append(repo)
                    if limit is not None and len(found) >= limit:
                        return found
        except ForgeNotFound:
            if owner is None:
                raise
            logger.warning("%s/%s not found; skipping it", forge.host, owner)
            result.missing_owners.append(owner)
    return found


# html_url -> (pushed_at, language, license, fork_source) from the last stored run
Known = dict[str, tuple[str | None, str | None, str | None, str | None]]


async def _add_details(get: Getter, forge: ForgeHost, projects: list[tuple[int, Repository]],
                       result: HostResult, known: Known | None = None) -> None:
    """Language, licence and fork parent: two calls. A failure leaves the fields empty.

    Projects with no activity since the last stored run keep their stored values,
    so only the first run pays for every project.
    """
    api = f"https://{forge.host}/api/v4"
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)
    known = known or {}

    async def one(project_id: int, repo: Repository) -> None:
        previous = known.get(repo.html_url)
        if previous and previous[0] == repo.pushed_at and (previous[1] or previous[2]):
            repo.language, repo.license = previous[1], previous[2]
            if previous[3]:
                repo.fork, repo.fork_source = True, previous[3]
            result.details_reused += 1
            return
        async with semaphore:
            try:
                languages, _ = await get(f"{api}/projects/{project_id}/languages", None)
                repo.language = main_language(languages)
                detail, _ = await get(f"{api}/projects/{project_id}", {"license": "true"})
                detail = detail if isinstance(detail, dict) else {}
                repo.license = (detail.get("license") or {}).get("key")
                # Anonymous project lists omit the fork parent; the single record has it.
                parent = detail.get("forked_from_project") or {}
                if parent.get("web_url"):
                    repo.fork, repo.fork_source = True, parent["web_url"]
            except Exception:  # noqa: BLE001 - details are best effort
                result.detail_failures += 1

    await asyncio.gather(*(one(pid, repo) for pid, repo in projects))


def _save_host(db: Database, forge: ForgeHost, repos: list[Repository], complete: bool) -> None:
    """Save repos; on a complete enumeration, mark this host's missing repos not current."""
    db.save_repositories(repos)
    if complete:
        if forge.owners:
            # A submission enumerates only its owner(s), never the whole shared host.
            # Use literal prefixes rather than LIKE: group names may contain '_' .
            for owner in forge.owners:
                key = f"{forge.host}/{owner}".lower()
                prefix = key + "/"
                db.conn.execute(
                    "UPDATE repositories SET current=0 WHERE "
                    "lower(owner)=? OR substr(lower(owner),1,?)=?",
                    (key, len(prefix), prefix),
                )
        else:
            db.conn.execute("UPDATE repositories SET current=0 WHERE html_url LIKE ?",
                            (f"https://{forge.host}/%",))
    db.conn.executemany("UPDATE repositories SET current=1 WHERE html_url=?",
                        [(r.html_url,) for r in repos])
    db.conn.commit()


def _known_details(db: Database, forge: ForgeHost) -> Known:
    rows = db.conn.execute(
        "SELECT html_url, pushed_at, language, license, fork_source FROM repositories "
        "WHERE html_url LIKE ?", (f"https://{forge.host}/%",)).fetchall()
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}


async def fetch_forge(get: Getter, forge: ForgeHost, limit: int | None = None,
                      details: bool = True, known: Known | None = None,
                      dedup: Dedup | None = None) -> HostResult:
    result = HostResult(host=forge.host, status="succeeded")
    if forge.platform == "forgejo":
        result.repos = await _list_forgejo(get, forge, limit, result, dedup)
        return result
    projects = await _list_projects(get, forge, limit, result, dedup)
    if details:
        await _add_details(get, forge, projects, result, known)
    result.repos = [repo for _, repo in projects]
    return result


def _dedup(db: Database, forge: ForgeHost) -> Dedup:
    rows = db.conn.execute(
        "SELECT html_url, owner, name FROM repositories WHERE COALESCE(current, 1) = 1 "
        "AND html_url NOT LIKE ?", (f"https://{forge.host}/%",)).fetchall()
    dedup = Dedup()
    for url, owner, name in rows:
        dedup.indexed_urls.add(normalise_url(url))
        if url.startswith("https://github.com/"):
            dedup.github_names.add((owner.lower(), name.lower()))
    return dedup


async def scrape_forges(
    db: Database,
    forges: list[ForgeHost],
    limit: int | None = None,
    details: bool = True,
    get: Getter | None = None,
    session: aiohttp.ClientSession | None = None,
) -> list[HostResult]:
    """Fetch and store every forge. One host failing doesn't stop the rest.

    Run it after the GitHub scrape, so copies of repos just refreshed there are skipped.

    Deliberately writes no scrape_runs row: publication reads the latest run as
    the GitHub scrape's completeness record.
    """
    if get is None:
        if session is None:
            raise ValueError("Pass a session or a get function")
        get = make_getter(session)

    async def one(forge: ForgeHost) -> HostResult:
        try:
            result = await fetch_forge(get, forge, limit=limit, details=details,
                                       known=_known_details(db, forge), dedup=_dedup(db, forge))
            # Synchronous, so no other host's write can interleave with this save.
            # A missing submitted group is not proof its old projects disappeared.
            _save_host(db, forge, result.repos,
                       complete=limit is None and not result.missing_owners)
        except Exception as exc:  # noqa: BLE001 - one host must not stop the rest
            db.conn.rollback()
            result = HostResult(host=forge.host, status="failed", error_type=type(exc).__name__)
            logger.error("Forge fetch failed for %s (%s: %s)", forge.host, result.error_type, exc)
        return result

    # Rate limits are per host, so hosts run side by side.
    return list(await asyncio.gather(*(one(f) for f in forges)))
