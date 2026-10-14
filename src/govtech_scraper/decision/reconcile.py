"""OpenRouter generation-cost reconciliation for already-sent requests."""

from __future__ import annotations

import asyncio
import json
import math
import re
from enum import Enum
from typing import Any, Callable, Protocol

import aiohttp

from ..db import Database
from .evidence import AccountingBreach, OpenRouterBudget, ensure_evidence_schema


GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{3,160}$")


class ReconcileResult(str, Enum):
    RECONCILED = "reconciled"
    ALREADY_RECONCILED = "already_reconciled"
    HELD = "held"


class GenerationLookupError(RuntimeError):
    """The local receipt is not safe to reconcile by generation lookup."""


class GenerationTransport(Protocol):
    async def get_generation(self, generation_id: str, *, api_key: str, timeout: float) -> dict: ...


class AioHttpGenerationTransport:
    def __init__(self, *, base_url: str = "https://openrouter.ai/api/v1"):
        self.base_url = base_url.rstrip("/")

    async def get_generation(self, generation_id: str, *, api_key: str, timeout: float) -> dict:
        headers = {"Authorization": f"Bearer {api_key}"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(
                f"{self.base_url}/generation",
                params={"id": generation_id},
                headers=headers,
            ) as response:
                text = await response.text()
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    data = {"raw_text": text[:2000]}
                return {"status": response.status, "data": data, "headers": dict(response.headers)}


class GenerationReconciler:
    def __init__(
        self,
        db: Database,
        *,
        api_key: str,
        transport: GenerationTransport | None = None,
        timeout: float = 30.0,
        sleep: Callable[[float], Any] | None = None,
    ):
        self.db = db
        self.conn = db.conn
        ensure_evidence_schema(self.conn)
        self.api_key = api_key
        self.transport = transport or AioHttpGenerationTransport()
        self.timeout = timeout
        self.sleep = sleep or asyncio.sleep

    async def reconcile(self, *, request_hash: str, max_attempts: int = 3) -> ReconcileResult:
        row = self.conn.execute(
            """SELECT l.id AS claim_id, l.status, l.reserved_usd, l.model,
                      r.generation_id, r.usage_json
               FROM openrouter_budget_ledger l
               JOIN openrouter_response_receipts r ON r.claim_id = l.id
               WHERE l.request_hash=?""",
            (request_hash,),
        ).fetchone()
        if row is None:
            raise GenerationLookupError(f"no receipt found for request hash {request_hash}")
        if row["status"] == "completed":
            return ReconcileResult.ALREADY_RECONCILED
        generation_id = row["generation_id"]
        if not generation_id or not GENERATION_ID_RE.fullmatch(generation_id):
            raise GenerationLookupError("invalid generation id for reconciliation")
        last_error = None
        for attempt in range(max(1, int(max_attempts))):
            response = await self.transport.get_generation(
                generation_id, api_key=self.api_key, timeout=self.timeout
            )
            status = int(response.get("status", 0))
            if status == 404 or status == 429 or status >= 500:
                last_error = f"generation lookup HTTP {status}"
                if attempt + 1 < max_attempts:
                    await self._sleep(min(30.0, 2**attempt))
                    continue
                self._hold(row["claim_id"], last_error)
                return ReconcileResult.HELD
            if status >= 400:
                self._hold(row["claim_id"], f"generation lookup HTTP {status}")
                return ReconcileResult.HELD
            data = response.get("data") or {}
            if not self._compatible(data, generation_id):
                self._hold(row["claim_id"], "generation lookup returned mismatched id")
                return ReconcileResult.HELD
            cost = self._cost(data)
            if cost is None:
                self._hold(row["claim_id"], "generation lookup omitted total_cost")
                return ReconcileResult.HELD
            if cost > float(row["reserved_usd"]) + 1e-12:
                self._hold(row["claim_id"], "generation lookup cost exceeds reservation")
                return ReconcileResult.HELD
            usage = json.loads(row["usage_json"] or "{}")
            usage["total_cost"] = cost
            usage.setdefault("generation_id", generation_id)
            try:
                OpenRouterBudget(self.db, ceiling_usd=self._ceiling(), run_id="reconcile").complete(
                    int(row["claim_id"]), usage
                )
            except AccountingBreach:
                raise
            return ReconcileResult.RECONCILED
        self._hold(row["claim_id"], last_error or "generation lookup unavailable")
        return ReconcileResult.HELD

    def _ceiling(self) -> float:
        row = self.conn.execute(
            "SELECT value FROM openrouter_budget_config WHERE key='ceiling_usd'"
        ).fetchone()
        return float(row["value"]) if row else 1.0

    def _hold(self, claim_id: int, error: str) -> None:
        self.conn.execute(
            "UPDATE openrouter_budget_ledger SET status='uncertain', error=? WHERE id=?",
            (error[:500], int(claim_id)),
        )
        self.conn.commit()

    @staticmethod
    def _compatible(data: dict, generation_id: str) -> bool:
        returned = data.get("id") or (data.get("data") or {}).get("id")
        return returned == generation_id

    @staticmethod
    def _cost(data: dict) -> float | None:
        for value in [
            (data.get("data") or {}).get("total_cost"),
            (data.get("usage") or {}).get("cost"),
            (data.get("usage") or {}).get("total_cost"),
            data.get("total_cost"),
        ]:
            try:
                cost = float(value) if value is not None else None
            except (TypeError, ValueError):
                cost = None
            if cost is not None and math.isfinite(cost) and cost >= 0:
                return cost
        return None

    async def _sleep(self, delay: float) -> None:
        result = self.sleep(delay)
        if hasattr(result, "__await__"):
            await result
