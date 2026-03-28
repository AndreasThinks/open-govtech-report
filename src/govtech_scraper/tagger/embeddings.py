"""Embedding provider abstraction for tag similarity."""

import asyncio
import logging
import math
from typing import Protocol, Optional

import aiohttp

logger = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors. Pure Python, no numpy."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""
    async def embed(self, texts: list[str], session: Optional[aiohttp.ClientSession] = None) -> list[list[float]]: ...
    async def embed_single(self, text: str, session: Optional[aiohttp.ClientSession] = None) -> list[float]: ...


class OpenRouterEmbeddings:
    """Embedding provider using OpenRouter's API."""

    def __init__(
        self,
        api_key: str,
        model: str = "openai/text-embedding-3-small",
        base_url: str = "https://openrouter.ai/api/v1",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def embed(
        self, 
        texts: list[str],
        session: Optional[aiohttp.ClientSession] = None,
    ) -> list[list[float]]:
        """Embed a batch of texts."""
        # Retry logic with exponential backoff
        max_retries = 3
        base_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                # Use provided session or create a temporary one
                if session:
                    return await self._make_request(session, texts)
                else:
                    async with aiohttp.ClientSession() as temp_session:
                        return await self._make_request(temp_session, texts)
            except aiohttp.ClientError as e:
                # Retry on network errors and timeouts
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Embedding request failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Embedding request failed after {max_retries} attempts: {e}")
                    raise
            except RuntimeError as e:
                # Check if it's a retryable HTTP error
                error_msg = str(e)
                if any(code in error_msg for code in ["429", "500", "502", "503", "504"]):
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"Embedding API error (attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"Embedding API error after {max_retries} attempts: {e}")
                        raise
                else:
                    # Non-retryable error (400, 401, 404, etc.)
                    raise

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        texts: list[str],
    ) -> list[list[float]]:
        """Make the actual API request."""
        async with session.post(
            f"{self.base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self.model, "input": texts},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Embedding API error: {resp.status} {body}")
            data = await resp.json()
            # Sort by index to preserve order
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in sorted_data]

    async def embed_single(
        self, 
        text: str,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> list[float]:
        """Embed a single text."""
        results = await self.embed([text], session=session)
        return results[0]
