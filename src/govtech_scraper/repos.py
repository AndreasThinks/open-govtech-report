"""Repository metadata fetching from GitHub API."""

import asyncio
import logging
from typing import Optional, Callable

import aiohttp
from aiolimiter import AsyncLimiter

from .auth import GitHubAuth
from .db import Database
from .models import GovernmentAccount, Repository
from .retry import retry_async

logger = logging.getLogger(__name__)

# GitHub API: 5000 req/hr for apps, but stay under secondary limits
# ~15 req/s is safe; we'll use 12/s to be conservative
RATE_LIMITER = AsyncLimiter(12, 1.0)

# Max concurrent connections
MAX_CONCURRENT = 20


def _parse_repo(data: dict, country: str) -> Repository:
    """Parse a GitHub API repo response into a Repository object."""
    license_info = data.get("license")
    license_name = license_info.get("spdx_id") if license_info else None

    fork_source = None
    if data.get("fork") and data.get("parent"):
        fork_source = data["parent"].get("full_name")

    return Repository(
        owner=data["owner"]["login"],
        name=data["name"],
        html_url=data["html_url"],
        country=country,
        description=data.get("description"),
        language=data.get("language"),
        stars=data.get("stargazers_count", 0),
        forks=data.get("forks_count", 0),
        watchers=data.get("watchers_count", 0),
        open_issues=data.get("open_issues_count", 0),
        size_kb=data.get("size", 0),
        license=license_name,
        topics=data.get("topics", []),
        default_branch=data.get("default_branch", "main"),
        fork=data.get("fork", False),
        archived=data.get("archived", False),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        pushed_at=data.get("pushed_at"),
    )


@retry_async(max_attempts=3, base_delay=2.0, retry_on=(aiohttp.ClientError, RuntimeError))
async def _fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict[str, str],
    params: Optional[dict] = None,
) -> tuple[list[dict], Optional[str]]:
    """Fetch a single page from GitHub API. Returns (items, next_url)."""
    async with RATE_LIMITER:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status == 304:
                return [], None  # Not modified
            if resp.status == 404:
                return [], None  # Account doesn't exist or has no repos
            if resp.status == 403:
                # Rate limited or secondary limit
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    wait = int(retry_after)
                    logger.warning(f"Rate limited. Waiting {wait}s...")
                    await asyncio.sleep(wait)
                    raise RuntimeError("Rate limited, retrying")
                remaining = resp.headers.get("X-RateLimit-Remaining", "?")
                logger.warning(f"403 Forbidden (remaining: {remaining})")
                raise RuntimeError(f"GitHub API 403: {await resp.text()}")
            if resp.status != 200:
                raise RuntimeError(f"GitHub API error: {resp.status}")

            data = await resp.json()
            # Parse Link header for pagination
            next_url = None
            link_header = resp.headers.get("Link", "")
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
            return data, next_url


async def fetch_repos_for_account(
    session: aiohttp.ClientSession,
    auth: GitHubAuth,
    account: GovernmentAccount,
    db: Database,
    force: bool = False,
) -> list[Repository]:
    """Fetch all repositories for a single government account."""
    token = await auth.get_token(session)
    headers = auth.get_headers(token)

    # Check cache unless forcing
    cache_url = f"https://api.github.com/users/{account.username}/repos"
    if not force:
        cached = db.get_cache(cache_url)
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]

    repos: list[Repository] = []
    url = cache_url
    params = {"per_page": "100", "type": "all", "sort": "updated"}

    while url:
        data, next_url = await _fetch_page(session, url, headers, params)
        if not data:
            break
        for item in data:
            repos.append(_parse_repo(item, account.country))
        url = next_url
        params = None  # params only on first request; next_url has them

    if repos:
        logger.debug(f"{account.username}: {len(repos)} repos")

    return repos


async def fetch_all_repos(
    session: aiohttp.ClientSession,
    auth: GitHubAuth,
    accounts: list[GovernmentAccount],
    db: Database,
    force: bool = False,
    limit: Optional[int] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> list[Repository]:
    """Fetch repositories for all government accounts with concurrency control."""
    if limit:
        accounts = accounts[:limit]

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    all_repos: list[Repository] = []

    async def _fetch_one(account: GovernmentAccount) -> list[Repository]:
        async with semaphore:
            try:
                repos = await fetch_repos_for_account(session, auth, account, db, force)
                if progress_callback:
                    progress_callback(1)
                return repos
            except Exception as e:
                logger.error(f"Failed to fetch {account.username}: {e}")
                if progress_callback:
                    progress_callback(1)
                return []

    tasks = [_fetch_one(account) for account in accounts]
    results = await asyncio.gather(*tasks)

    for repo_list in results:
        all_repos.extend(repo_list)

    return all_repos
