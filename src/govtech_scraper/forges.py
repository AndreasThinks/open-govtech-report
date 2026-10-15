"""Repositories on government forges outside GitHub, via ecosyste.ms.

ecosyste.ms already indexes self-hosted GitLab, Gitea and other forges, so one
client covers them all. Its data is CC BY-SA 4.0: anything published from these
rows needs attribution and share-alike.

The free anonymous tier allows 300 requests an hour; the limiter below stays
inside it. Pages are 100 repos, so a 3,000-repo host costs about 30 requests.
"""

import asyncio
import logging
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

ECOSYSTEMS_API = "https://repos.ecosyste.ms/api/v1"
FORGE_HOSTS_PATH = Path(__file__).parent / "forge_hosts.yaml"
USER_AGENT = "open-govtech-index (+https://govtechindex.cloud)"
PER_PAGE = 100
MAX_PAGES = 1000

# 300 requests/hour anonymous; keep a margin.
REQUESTS_PER_HOUR = 280

# ecosyste.ms keeps rows for repos the forge no longer serves.
_GONE_STATUSES = {"not_found", "removed", "hidden"}


@dataclass
class ForgeHost:
    """A government forge as ecosyste.ms names it."""
    host: str
    country: str
    level: str = "national"
    description: str = ""
    owners: list[str] = field(default_factory=list)


@dataclass
class HostResult:
    host: str
    status: str  # "succeeded" or "failed"
    repos: list[Repository] = field(default_factory=list)
    skipped_mirrors: int = 0
    skipped_gone: int = 0
    error_type: str | None = None


PageFetcher = Callable[[str, dict], Awaitable[list[dict]]]


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
        ))
    return hosts


def parse_ecosystems_repo(data: dict, forge: ForgeHost) -> Repository | None:
    """Map an ecosyste.ms repository record onto Repository.

    Returns None for records that don't belong to the forge (defensive) or
    that lack a usable URL.
    """
    html_url = (data.get("html_url") or "").rstrip("/")
    full_name = data.get("full_name") or ""
    if not html_url.startswith(f"https://{forge.host}/") or "/" not in full_name:
        return None
    # GitLab groups nest: keep the whole group path as the owner.
    owner, name = full_name.rsplit("/", 1)
    return Repository(
        owner=owner,
        name=name,
        html_url=html_url,
        country=forge.country,
        description=data.get("description"),
        language=data.get("language"),
        stars=data.get("stargazers_count") or 0,
        forks=data.get("forks_count") or 0,
        watchers=data.get("subscribers_count") or 0,
        open_issues=data.get("open_issues_count") or 0,
        size_kb=data.get("size") or 0,
        license=data.get("license"),
        topics=list(data.get("topics") or []),
        default_branch=data.get("default_branch") or "main",
        fork=bool(data.get("fork")),
        archived=bool(data.get("archived")),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        pushed_at=data.get("pushed_at"),
        fork_source=data.get("source_name"),
    )


def _make_page_fetcher(session: aiohttp.ClientSession) -> PageFetcher:
    limiter = AsyncLimiter(REQUESTS_PER_HOUR, 3600)

    @retry_async(max_attempts=4, base_delay=5.0,
                 retry_on=(aiohttp.ClientError, RuntimeError, asyncio.TimeoutError))
    async def fetch(url: str, params: dict) -> list[dict]:
        async with limiter, session.get(url, params=params,
                               headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status == 429:
                wait = min(300, int(resp.headers.get("Retry-After", "60") or 60))
                logger.warning("ecosyste.ms rate limited; waiting %ss", wait)
                await asyncio.sleep(wait)
                raise RuntimeError("ecosyste.ms 429")
            if resp.status != 200:
                raise RuntimeError(f"ecosyste.ms error {resp.status}")
            data = await resp.json()
        if not isinstance(data, list):
            raise RuntimeError("Invalid ecosyste.ms repository response")
        return data
    return fetch


async def fetch_forge_repos(
    fetch_page: PageFetcher,
    forge: ForgeHost,
    limit: int | None = None,
    include_mirrors: bool = False,
) -> HostResult:
    """Enumerate every repository on one host (or its listed owners)."""
    host_path = quote(forge.host, safe="")
    if forge.owners:
        urls = [f"{ECOSYSTEMS_API}/hosts/{host_path}/owners/{quote(o, safe='')}/repositories"
                for o in forge.owners]
    else:
        urls = [f"{ECOSYSTEMS_API}/hosts/{host_path}/repositories"]

    result = HostResult(host=forge.host, status="succeeded")
    seen: set[str] = set()
    for url in urls:
        for page in range(1, MAX_PAGES + 1):
            params = {"per_page": PER_PAGE, "page": page, "sort": "created_at", "order": "asc"}
            items = await fetch_page(url, params)
            for item in items:
                if item.get("status") in _GONE_STATUSES:
                    result.skipped_gone += 1
                    continue
                if item.get("mirror_url") and not include_mirrors:
                    result.skipped_mirrors += 1
                    continue
                repo = parse_ecosystems_repo(item, forge)
                if repo is None or repo.html_url in seen:
                    continue
                seen.add(repo.html_url)
                result.repos.append(repo)
                if limit is not None and len(result.repos) >= limit:
                    return result
            if len(items) < PER_PAGE:
                break
        else:
            raise RuntimeError(f"Pagination exceeded {MAX_PAGES} pages for {forge.host}")
    return result


def _save_host(db: Database, forge: ForgeHost, repos: list[Repository], complete: bool) -> None:
    """Save repos; on a complete enumeration, mark this host's missing repos not current."""
    db.save_repositories(repos)
    if complete:
        db.conn.execute("UPDATE repositories SET current=0 WHERE html_url LIKE ?",
                        (f"https://{forge.host}/%",))
    db.conn.executemany("UPDATE repositories SET current=1 WHERE html_url=?",
                        [(r.html_url,) for r in repos])
    db.conn.commit()


async def scrape_forges(
    db: Database,
    forges: list[ForgeHost],
    limit: int | None = None,
    fetch_page: PageFetcher | None = None,
    session: aiohttp.ClientSession | None = None,
) -> list[HostResult]:
    """Fetch and store each forge in turn. One host failing doesn't stop the rest.

    Deliberately writes no scrape_runs row: publication reads the latest run as
    the GitHub scrape's completeness record.
    """
    if fetch_page is None:
        if session is None:
            raise ValueError("Pass a session or a fetch_page function")
        fetch_page = _make_page_fetcher(session)

    results = []
    for forge in forges:
        try:
            result = await fetch_forge_repos(fetch_page, forge, limit=limit)
            _save_host(db, forge, result.repos, complete=limit is None)
        except Exception as exc:
            db.conn.rollback()
            result = HostResult(host=forge.host, status="failed", error_type=type(exc).__name__)
            logger.error("Forge fetch failed for %s (%s)", forge.host, result.error_type)
        results.append(result)
    return results
