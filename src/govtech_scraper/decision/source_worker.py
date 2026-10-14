"""Recoverable public README source-cache worker."""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

import aiohttp

from ..db import Database
from .evidence import EvidenceStore, build_graphql_readme_query, decode_graphql_readmes, source_from_repo, fetch_readme_source
from .store import row_to_repository


class AuthBlocked(RuntimeError):
    """GitHub rejected authentication or authorization; operator action is required."""


@dataclass
class SourceWorkerStatus:
    phase: str
    cached: int = 0
    waiting: int = 0
    failed: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    final_error: str | None = None
    last_committed_count: int = 0
    next_retry_time: str | None = None


@dataclass
class GraphQLResponse:
    status: int
    payload: dict[str, Any]
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class GraphQLTransport(Protocol):
    async def post_graphql(self, query: str, *, token: str, timeout: float) -> GraphQLResponse: ...


class AioHttpGraphQLTransport:
    async def post_graphql(self, query: str, *, token: str, timeout: float) -> GraphQLResponse:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(
                "https://api.github.com/graphql",
                headers=headers,
                json={"query": query},
            ) as response:
                text = await response.text()
                try:
                    payload = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    payload = {}
                return GraphQLResponse(
                    status=response.status,
                    payload=payload,
                    text=text,
                    headers={k: v for k, v in response.headers.items()},
                )


class GraphQLSourceWorker:
    def __init__(
        self,
        db: Database,
        *,
        token_provider: Callable[[], str],
        transport: GraphQLTransport | None = None,
        status_path: str | Path | None = None,
        initial_batch_size: int = 40,
        min_batch_size: int = 10,
        max_attempts_per_batch: int = 3,
        max_total_requests: int = 10_000,
        timeout: float = 60.0,
        sleep: Callable[[float], Any] | None = None,
    ):
        self.db = db
        self.store = EvidenceStore(db)
        self.token_provider = token_provider
        self.transport = transport or AioHttpGraphQLTransport()
        self.status_path = Path(status_path) if status_path else None
        self.initial_batch_size = min(40, max(1, int(initial_batch_size)))
        self.min_batch_size = max(1, int(min_batch_size))
        self.max_attempts_per_batch = max(1, int(max_attempts_per_batch))
        self.max_total_requests = max(1, int(max_total_requests))
        self.timeout = timeout
        self.sleep = sleep or asyncio.sleep
        self._request_count = 0

    async def run(self, *, limit: int | None = None) -> SourceWorkerStatus:
        status = SourceWorkerStatus(phase="running")
        self._write_status(status)
        repos = self._uncached_repos(limit)
        queue: list[list[Any]] = [
            repos[index : index + self.initial_batch_size]
            for index in range(0, len(repos), self.initial_batch_size)
        ]
        try:
            while queue and self._request_count < self.max_total_requests:
                batch = queue.pop(0)
                outcome = await self._fetch_batch(batch)
                if outcome == "ok":
                    status.cached = self._cached_count
                    status.last_committed_count = self._cached_count
                    status.statuses = dict(self._status_counts)
                elif outcome == "split":
                    if len(batch) <= self.min_batch_size:
                        queue.append(batch)
                        break
                    midpoint = max(self.min_batch_size, len(batch) // 2)
                    queue.insert(0, batch[midpoint:])
                    queue.insert(0, batch[:midpoint])
                status.waiting = sum(len(item) for item in queue)
                self._write_status(status)
            if queue:
                status.phase = "partial"
                status.waiting = sum(len(item) for item in queue)
                if status.final_error is None:
                    status.final_error = self._last_error
            else:
                status.phase = "completed"
                status.waiting = 0
            self._write_status(status)
            return status
        except AuthBlocked as exc:
            status.phase = "blocked"
            status.final_error = str(exc)
            status.waiting = sum(len(item) for item in queue)
            self._write_status(status)
            raise
        except Exception as exc:
            status.phase = "partial"
            status.final_error = str(exc)
            status.waiting = len(batch) + sum(len(item) for item in queue) if "batch" in locals() else len(repos)
            self._write_status(status)
            return status

    @property
    def _cached_count(self) -> int:
        return int(self.db.conn.execute("SELECT COUNT(*) FROM evidence_sources").fetchone()[0])

    @property
    def _status_counts(self) -> dict[str, int]:
        rows = self.db.conn.execute(
            "SELECT readme_status, COUNT(*) AS count FROM evidence_sources GROUP BY readme_status"
        ).fetchall()
        return {row["readme_status"]: int(row["count"]) for row in rows}

    def _uncached_repos(self, limit: int | None) -> list[Any]:
        sql = (
            "SELECT * FROM repositories WHERE COALESCE(current,1)=1 AND COALESCE(fork,0)=0 "
            "ORDER BY stars DESC"
        )
        rows = self.db.conn.execute(sql).fetchall()
        repos = []
        for row in rows:
            repo = row_to_repository(row)
            if self.store.load_cached_source(repo) is None:
                repos.append(repo)
            if limit is not None and len(repos) >= limit:
                break
        return repos

    async def _fetch_batch(self, batch: list[Any]) -> str:
        query = build_graphql_readme_query(batch)
        self._last_error = None
        token = self.token_provider()
        for attempt in range(self.max_attempts_per_batch):
            self._request_count += 1
            try:
                response = await self.transport.post_graphql(query, token=token, timeout=self.timeout)
            except Exception as exc:
                self._last_error = str(exc)
                if attempt + 1 < self.max_attempts_per_batch:
                    await self._sleep_backoff(attempt, None)
                    continue
                return "split"
            if response.status in (401, 403):
                raise AuthBlocked(f"GitHub GraphQL authentication blocked with HTTP {response.status}")
            if response.status == 429 or response.status in (502, 503, 504):
                self._last_error = f"GitHub GraphQL transient HTTP {response.status}"
                if attempt + 1 < self.max_attempts_per_batch:
                    await self._sleep_backoff(attempt, response.headers.get("Retry-After"))
                    continue
                return "split"
            if response.status >= 400:
                self._last_error = f"GitHub GraphQL HTTP {response.status}: {response.text[:200]}"
                return "split"
            decoded = decode_graphql_readmes(response.payload, batch)
            for repo, text, sha, readme_status in decoded:
                if readme_status == "inaccessible":
                    continue
                if readme_status == "needs_rest":
                    text, sha, readme_status = await self._fetch_rest_readme(repo, token)
                source = source_from_repo(repo, text, sha, readme_status)
                self.store.save_source(source)
                self.db.conn.commit()
            return "ok"
        return "split"

    async def _fetch_rest_readme(self, repo, token):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
            return await fetch_readme_source(session, repo, token=token)

    async def _sleep_backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = min(60.0, max(0.0, float(retry_after)))
            except ValueError:
                delay = 0.0
        else:
            delay = min(30.0, (2**attempt) + random.random())
        result = self.sleep(delay)
        if hasattr(result, "__await__"):
            await result

    def _write_status(self, status: SourceWorkerStatus) -> None:
        if self.status_path is None:
            return
        payload = asdict(status)
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_suffix(self.status_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        tmp.replace(self.status_path)
