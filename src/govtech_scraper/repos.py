"""Repository metadata fetching from GitHub API."""

import asyncio
import logging
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
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

# Owner names can repeat on other forges (see forges.py); scope owner updates to GitHub.
GITHUB_URL_PATTERN = "https://github.com/%"


class AccountNotFound(Exception):
    """A confirmed HTTP 404, distinct from network or permission failure."""



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
        fork_source=fork_source,
    )


@retry_async(max_attempts=3, base_delay=2.0, retry_on=(aiohttp.ClientError, RuntimeError, TimeoutError))
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
                raise RuntimeError("Unexpected 304 without a complete cached enumeration")
            if resp.status == 404:
                raise AccountNotFound()
            if resp.status == 403:
                # Rate limited or secondary limit
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    wait = min(60, max(0, int(retry_after)))
                    logger.warning(f"Rate limited. Waiting {wait}s...")
                    await asyncio.sleep(wait)
                    raise RuntimeError("Rate limited, retrying")
                remaining = resp.headers.get("X-RateLimit-Remaining", "?")
                logger.warning(f"403 Forbidden (remaining: {remaining})")
                raise RuntimeError("GitHub API 403")
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

    # A first-page ETag cannot prove that later pages are unchanged. Until
    # complete page bodies are cached, enumerate unconditionally, even without --force.
    cache_url = f"https://api.github.com/users/{account.username}/repos"

    repos: list[Repository] = []
    url = cache_url
    params = {"per_page": "100", "type": "all", "sort": "updated"}

    seen_pages = set()
    while url:
        if url in seen_pages or len(seen_pages) >= 10000:
            raise RuntimeError("Invalid or excessive pagination")
        seen_pages.add(url)
        try:
            data, next_url = await _fetch_page(session, url, headers, params)
        except AccountNotFound:
            if len(seen_pages) > 1:
                raise RuntimeError('Pagination failed after account enumeration started') from None
            raise
        if not isinstance(data, list):
            raise RuntimeError("Invalid repository response")
        for item in data:
            repos.append(_parse_repo(item, account.country))
        if next_url and not next_url.startswith('https://api.github.com/'):
            raise RuntimeError('Invalid pagination origin')
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
    expected = len(accounts)
    if limit is not None:
        accounts = accounts[:limit]
    run_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    db.conn.execute('INSERT INTO scrape_runs(id,started_at,expected,attempted) VALUES (?,?,?,?)',
                    (run_id, now, expected, len(accounts)))
    db.conn.commit()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def _fetch_one(account):
        status, error_type, repos = 'failed', None, []
        async with semaphore:
            try:
                repos = await fetch_repos_for_account(session, auth, account, db, force)
                old = db.conn.execute('SELECT * FROM repositories WHERE lower(owner)=lower(?) AND current=1 AND html_url LIKE ?', (account.username, GITHUB_URL_PATTERN)).fetchall()
                old_by_url = {r['html_url']: r for r in old}
                def equal(repo):
                    row = old_by_url.get(repo.html_url)
                    if row is None:
                        return False
                    values = asdict(repo)
                    values['topics'] = json.dumps(repo.topics)
                    return all(row[k] == v for k, v in values.items())
                status = 'unchanged' if len(old) == len(repos) and all(equal(r) for r in repos) else 'succeeded'
                # Save and mark current only after this account's entire enumeration succeeds.
                db.save_repositories(repos)
                db.conn.execute('UPDATE repositories SET current=0 WHERE lower(owner)=lower(?) AND html_url LIKE ?', (account.username, GITHUB_URL_PATTERN))
                db.conn.executemany('UPDATE repositories SET current=1 WHERE html_url=?', [(r.html_url,) for r in repos])
            except AccountNotFound:
                status = 'not-found'
                # Keep prior repositories and snapshots; a 404 may be transient.
            except Exception as exc:
                status = 'failed'
                db.conn.rollback()
                error_type = type(exc).__name__
                logger.error('Account fetch failed (%s)', error_type)
            finally:
                db.conn.execute('INSERT INTO account_fetches VALUES (?,?,?,?,?,?)',
                                (run_id, account.username, status, len(repos), error_type,
                                 datetime.now(timezone.utc).isoformat()))
                db.conn.commit()
                if progress_callback:
                    progress_callback(1)
        return status, repos

    results = await asyncio.gather(*[_fetch_one(account) for account in accounts])
    failures = sum(status == 'failed' for status, _ in results)
    complete = bool(expected and len(accounts) == expected and not failures)
    db.conn.execute('UPDATE scrape_runs SET finished_at=?, complete=? WHERE id=?',
                    (datetime.now(timezone.utc).isoformat(), int(complete), run_id))
    db.conn.commit()
    if failures or not expected:
        raise RuntimeError(f'Scrape incomplete: {failures} failed accounts; history retained')
    return [repo for _, items in results for repo in items]
