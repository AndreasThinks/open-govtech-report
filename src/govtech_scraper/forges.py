"""Repositories on government GitLab servers outside GitHub.

Each server's own GitLab REST API is read anonymously: public projects need no
token. The project list carries last activity, archived state, stars, topics and
whether the owner is a group or a person; language and licence take one extra
call each per project, so they are optional.

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


@dataclass
class ForgeHost:
    """A government GitLab server."""
    host: str
    country: str
    level: str = "national"
    description: str = ""
    # Top-level groups to limit a shared server (e.g. gitlab.com) to.
    owners: list[str] = field(default_factory=list)
    include_personal: bool = False


@dataclass
class HostResult:
    host: str
    status: str  # "succeeded" or "failed"
    repos: list[Repository] = field(default_factory=list)
    skipped_personal: int = 0
    detail_failures: int = 0
    details_reused: int = 0
    error_type: str | None = None


# get(url, params) -> (decoded JSON, next page URL or None)
Getter = Callable[[str, dict | None], Awaitable[tuple[object, str | None]]]


def load_forge_hosts(path: Path = FORGE_HOSTS_PATH) -> list[ForgeHost]:
    data = yaml.safe_load(path.read_text())
    hosts = []
    for entry in data.get("hosts", []):
        hosts.append(ForgeHost(
            host=str(entry["host"]).strip().lower(),
            country=str(entry["country"]).strip(),
            level=str(entry.get("level", "national")),
            description=str(entry.get("description", "")),
            owners=[str(o).strip() for o in entry.get("owners") or []],
            include_personal=bool(entry.get("include_personal", False)),
        ))
    return hosts


def parse_gitlab_project(data: dict, forge: ForgeHost) -> Repository | None:
    """Map a GitLab project record onto Repository.

    Returns None for records whose URL isn't on the forge (defensive).
    """
    html_url = (data.get("web_url") or "").rstrip("/")
    full_name = data.get("path_with_namespace") or ""
    if not html_url.startswith(f"https://{forge.host}/") or "/" not in full_name:
        return None
    # GitLab groups nest: keep the whole group path as the owner.
    owner, name = full_name.rsplit("/", 1)
    parent = data.get("forked_from_project") or {}
    last_activity = data.get("last_activity_at")
    return Repository(
        owner=owner,
        name=name,
        html_url=html_url,
        country=forge.country,
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
                raise RuntimeError("GitLab 429")
            if resp.status != 200:
                raise RuntimeError(f"GitLab error {resp.status}")
            if resp.headers.get("RateLimit-Remaining") == "0":
                # GitLab sends RateLimit-Reset as a Unix timestamp.
                try:
                    wait = float(resp.headers.get("RateLimit-Reset", "")) - time.time()
                except ValueError:
                    wait = 60
                paused_until[host] = loop.time() + max(0.0, min(MAX_PAUSE_SECONDS, wait))
            return await resp.json(), _next_link(resp.headers.get("Link", ""))
    return get


async def _list_projects(get: Getter, forge: ForgeHost, limit: int | None,
                         result: HostResult) -> list[tuple[int, Repository]]:
    api = f"https://{forge.host}/api/v4"
    params = {"per_page": PER_PAGE, "pagination": "keyset", "order_by": "id", "sort": "asc",
              "visibility": "public"}
    if forge.owners:
        starts = [(f"{api}/groups/{quote(o, safe='')}/projects",
                   {**params, "include_subgroups": "true"}) for o in forge.owners]
    else:
        starts = [(f"{api}/projects", params)]

    found: list[tuple[int, Repository]] = []
    seen: set[str] = set()
    for url, first_params in starts:
        next_url: str | None = url
        page_params: dict | None = first_params
        for _ in range(MAX_PAGES):
            if next_url is None:
                break
            items, next_url = await get(next_url, page_params)
            page_params = None  # the next link carries them
            if not isinstance(items, list):
                raise RuntimeError("Invalid GitLab project list")
            if next_url and not next_url.startswith(f"{api}/"):
                raise RuntimeError("Invalid pagination origin")
            for item in items:
                if (item.get("namespace") or {}).get("kind") == "user" \
                        and not forge.include_personal:
                    result.skipped_personal += 1
                    continue
                repo = parse_gitlab_project(item, forge)
                if repo is None or repo.html_url in seen:
                    continue
                seen.add(repo.html_url)
                found.append((item["id"], repo))
                if limit is not None and len(found) >= limit:
                    return found
        else:
            raise RuntimeError(f"Pagination exceeded {MAX_PAGES} pages for {forge.host}")
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
                      details: bool = True, known: Known | None = None) -> HostResult:
    result = HostResult(host=forge.host, status="succeeded")
    projects = await _list_projects(get, forge, limit, result)
    if details:
        await _add_details(get, forge, projects, result, known)
    result.repos = [repo for _, repo in projects]
    return result


async def scrape_forges(
    db: Database,
    forges: list[ForgeHost],
    limit: int | None = None,
    details: bool = True,
    get: Getter | None = None,
    session: aiohttp.ClientSession | None = None,
) -> list[HostResult]:
    """Fetch and store every forge. One host failing doesn't stop the rest.

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
                                       known=_known_details(db, forge))
            # Synchronous, so no other host's write can interleave with this save.
            _save_host(db, forge, result.repos, complete=limit is None)
        except Exception as exc:  # noqa: BLE001 - one host must not stop the rest
            db.conn.rollback()
            result = HostResult(host=forge.host, status="failed", error_type=type(exc).__name__)
            logger.error("Forge fetch failed for %s (%s)", forge.host, result.error_type)
        return result

    # Rate limits are per host, so hosts run side by side.
    return list(await asyncio.gather(*(one(f) for f in forges)))
