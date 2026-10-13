"""Clients for System One decision models.

TypeSafe's hosted Jev and the open-weight Kev models speak the same request format
(POST /v1/systemone), so one HTTP client covers both. A second client calls our own
Hugging Face ZeroGPU Space (spaces/decision-scorer), which batches many requests into a
single GPU allocation.

Every client returns one response dict per request, in order. A request that failed
for its own reasons comes back as {"error": "..."} so one bad repo never sinks a batch.
Errors that will hit every later request too (no credit, no quota) raise instead.
"""

import asyncio
import json
import logging
import random
import time
from typing import Protocol

import aiohttp

logger = logging.getLogger(__name__)

JEV_BASE_URL = "https://api.typesafe.ai"

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 529}


class BackendExhaustedError(RuntimeError):
    """The backend refuses all further work (credits or GPU quota used up). Re-run later to resume."""


class DecisionClient(Protocol):
    model: str

    async def decide_many(self, requests: list[dict]) -> list[dict]: ...


def _backoff(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    return min(cap, base * 2**attempt) * (0.5 + random.random() / 2)


class HttpSystemOneClient:
    """Calls a /v1/systemone endpoint: hosted Jev, a local `kev.serve`, or a Kev endpoint on Modal."""

    def __init__(
        self,
        base_url: str = JEV_BASE_URL,
        api_key: str | None = None,
        model: str = "jev-latest",
        concurrency: int = 8,
        max_attempts: int = 5,
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.concurrency = concurrency
        self.max_attempts = max_attempts
        self.timeout = timeout

    async def decide_many(self, requests: list[dict]) -> list[dict]:
        semaphore = asyncio.Semaphore(self.concurrency)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:

            async def one(request: dict) -> dict:
                async with semaphore:
                    return await self._post(session, {**request, "model": self.model})

            return list(await asyncio.gather(*(one(r) for r in requests)))

    async def _post(self, session: aiohttp.ClientSession, body: dict) -> dict:
        url = f"{self.base_url}/v1/systemone"
        last_error = "no attempt made"
        for attempt in range(self.max_attempts):
            try:
                async with session.post(url, json=body) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status in (401, 402, 403):
                        raise BackendExhaustedError(
                            f"System One endpoint refused the request (HTTP {resp.status}); "
                            "check the API key or remaining credit"
                        )
                    last_error = f"HTTP {resp.status}"
                    if resp.status not in _RETRYABLE_STATUS:
                        return {"error": last_error}
            except (TimeoutError, aiohttp.ClientError) as e:
                last_error = type(e).__name__
            if attempt < self.max_attempts - 1:
                await asyncio.sleep(_backoff(attempt))
        return {"error": f"gave up after {self.max_attempts} attempts: {last_error}"}


class SpaceClient:
    """Calls the batch endpoint of our own ZeroGPU Space with gradio_client.

    Requests made with your Hugging Face token use your own ZeroGPU quota (40 GPU-minutes
    a day on PRO). When it runs out, BackendExhaustedError stops the run; progress is saved.
    """

    def __init__(
        self,
        space_id: str,
        hf_token: str | None = None,
        batch_size: int = 64,
        api_name: str = "/score_batch",
        max_attempts: int = 4,
    ):
        self.space_id = space_id
        self.hf_token = hf_token
        self.batch_size = batch_size
        self.api_name = api_name
        self.max_attempts = max_attempts
        self.model = f"space:{space_id}"
        self._client = None

    def _gradio(self):
        if self._client is None:
            try:
                from gradio_client import Client
            except ImportError as e:
                raise RuntimeError(
                    "The Space backend needs gradio_client: uv sync --extra decision"
                ) from e
            self._client = Client(self.space_id, token=self.hf_token, verbose=False)
        return self._client

    def _call(self, batch: list[dict]) -> list[dict]:
        last_error = "no attempt made"
        for attempt in range(self.max_attempts):
            try:
                raw = self._gradio().predict(json.dumps(batch), api_name=self.api_name)
                responses = json.loads(raw)
                if len(responses) != len(batch):
                    raise ValueError(
                        f"Space returned {len(responses)} answers for {len(batch)} requests"
                    )
                return responses
            except Exception as e:  # gradio_client raises plain exceptions with the Space's message
                message = str(e)
                if "quota" in message.lower():
                    raise BackendExhaustedError(f"ZeroGPU quota exhausted: {message}") from e
                last_error = f"{type(e).__name__}: {message[:200]}"
                logger.warning(
                    f"Space call failed (attempt {attempt + 1}/{self.max_attempts}): {last_error}"
                )
            if attempt < self.max_attempts - 1:
                time.sleep(_backoff(attempt, base=5.0))
        return [{"error": last_error}] * len(batch)

    async def decide_many(self, requests: list[dict]) -> list[dict]:
        results: list[dict] = []
        for start in range(0, len(requests), self.batch_size):
            batch = requests[start : start + self.batch_size]
            results.extend(await asyncio.to_thread(self._call, batch))
        return results
