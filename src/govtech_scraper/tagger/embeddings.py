"""Embedding provider abstraction for tag similarity."""

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
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_single(self, text: str) -> list[float]: ...


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

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": texts},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"Embedding API error: {resp.status} {body}")
                data = await resp.json()
                # Sort by index to preserve order
                sorted_data = sorted(data["data"], key=lambda x: x["index"])
                return [item["embedding"] for item in sorted_data]

    async def embed_single(self, text: str) -> list[float]:
        """Embed a single text."""
        results = await self.embed([text])
        return results[0]
