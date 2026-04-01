"""Step 2: Hybrid tag deduplication and reconciliation.

Candidate pairs are generated via two independent signals (OR'd together):
  1. Embedding cosine similarity >= embedding_threshold (default 0.80)
  2. String similarity (rapidfuzz token_sort_ratio) >= string_threshold (default 0.82)

Both signals feed into the same LLM confirmation step, which makes the final
merge/keep decision. This catches semantic duplicates (embedding) AND
punctuation/hyphen/plural variants (string) that embeddings miss.
"""

import asyncio
import json
import logging
from typing import Optional

import aiohttp
from rapidfuzz import fuzz

from .schemas import TaggingResult, DeduplicationDecision
from .taxonomy import Taxonomy
from .embeddings import EmbeddingProvider
from .prompts import DEDUP_SYSTEM, DEDUP_USER

logger = logging.getLogger(__name__)

# String similarity threshold for hybrid dedup (rapidfuzz token_sort_ratio, 0–100 scale)
_STRING_THRESHOLD = 82


def _string_similar(a: str, b: str, threshold: int = _STRING_THRESHOLD) -> bool:
    """Return True if tags are string-similar enough to warrant LLM review."""
    return fuzz.token_sort_ratio(a, b) >= threshold


class TagReconciler:
    """Reconciles suggested tags against the existing taxonomy using hybrid similarity."""

    def __init__(
        self,
        taxonomy: Taxonomy,
        embedding_provider: EmbeddingProvider,
        api_key: str,
        model: str = "qwen/qwen3-32b",
        base_url: str = "https://openrouter.ai/api/v1",
        similarity_threshold: float = 0.80,
        string_threshold: int = _STRING_THRESHOLD,
    ):
        self.taxonomy = taxonomy
        self.embeddings = embedding_provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.similarity_threshold = similarity_threshold
        self.string_threshold = string_threshold

    async def reconcile(
        self,
        suggestions: TaggingResult,
        repo_summary: str,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> list[str]:
        """Reconcile suggested tags against taxonomy.
        
        For each suggested tag:
        1. Exact match in taxonomy -> keep as-is, increment usage
        2. Embedding similarity above threshold -> ask LLM if same concept
        3. No match -> new tag, add to taxonomy
        
        Returns final list of reconciled tag strings.
        """
        final_tags: list[str] = []

        # Get embeddings for all suggested tags at once (batch efficiency)
        tag_strings = [s.tag for s in suggestions.tags]
        if not tag_strings:
            return final_tags

        tag_embeddings = await self.embeddings.embed(tag_strings, session=session)

        for suggestion, embedding in zip(suggestions.tags, tag_embeddings):
            tag = suggestion.tag

            # 1. Exact match
            if self.taxonomy.has_tag(tag):
                self.taxonomy.increment_usage(tag)
                final_tags.append(tag)
                logger.debug(f"Exact match: {tag}")
                continue

            # 2. Hybrid similarity — embedding OR string, whichever fires first
            # 2a. Embedding similarity (semantic)
            similar = self.taxonomy.find_similar(
                embedding, threshold=self.similarity_threshold
            )

            best_match: Optional[str] = None
            best_score: float = 0.0
            match_signal: str = ""

            if similar:
                best_match, best_score = similar[0]
                match_signal = "embedding"
                logger.debug(f"Embedding match for '{tag}': '{best_match}' (score={best_score:.3f})")

            # 2b. String similarity (catches hyphen/plural/punctuation variants)
            if best_match is None:
                for existing_tag in self.taxonomy.all_tags():
                    if _string_similar(tag, existing_tag, self.string_threshold):
                        best_match = existing_tag
                        best_score = fuzz.token_sort_ratio(tag, existing_tag) / 100.0
                        match_signal = "string"
                        logger.debug(f"String match for '{tag}': '{best_match}' (ratio={best_score:.2f})")
                        break

            if best_match is not None:
                logger.debug(f"Candidate match for '{tag}': '{best_match}' via {match_signal}")

                # Ask LLM if they're the same concept
                decision = await self._ask_dedup(
                    new_tag=tag,
                    existing_tag=best_match,
                    repo_summary=repo_summary,
                    usage_count=self.taxonomy.get_tag(best_match).usage_count
                    if self.taxonomy.get_tag(best_match)
                    else 0,
                    session=session,
                )

                if decision.same_concept:
                    # Use the preferred tag (usually the existing one)
                    preferred = decision.preferred_tag
                    if preferred == tag and self.taxonomy.has_tag(best_match):
                        # LLM prefers the new name — merge old into new
                        self.taxonomy.add_tag(tag, embedding=embedding)
                        self.taxonomy.merge_tag(best_match, tag)
                        final_tags.append(tag)
                    else:
                        # Keep existing tag
                        self.taxonomy.increment_usage(best_match)
                        final_tags.append(best_match)
                    logger.info(
                        f"Dedup: '{tag}' -> '{preferred}' (score={best_score:.3f})"
                    )
                    continue

            # 3. New tag
            self.taxonomy.add_tag(tag, embedding=embedding)
            final_tags.append(tag)
            logger.info(f"New tag: {tag}")

        return final_tags

    async def _ask_dedup(
        self,
        new_tag: str,
        existing_tag: str,
        repo_summary: str,
        usage_count: int,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> DeduplicationDecision:
        """Ask LLM whether two tags should be merged."""
        user_prompt = DEDUP_USER.format(
            new_tag=new_tag,
            existing_tag=existing_tag,
            repo_summary=repo_summary,
            usage_count=usage_count,
        )

        response_schema = DeduplicationDecision.model_json_schema()

        # Retry logic with exponential backoff
        max_retries = 3
        base_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                # Use provided session or create a temporary one
                if session:
                    return await self._make_request(
                        session, user_prompt, response_schema
                    )
                else:
                    async with aiohttp.ClientSession() as temp_session:
                        return await self._make_request(
                            temp_session, user_prompt, response_schema
                        )
            except aiohttp.ClientError as e:
                # Retry on network errors and timeouts
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Dedup request failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Dedup request failed after {max_retries} attempts: {e}")
                    # Return default decision on final failure
                    return DeduplicationDecision(
                        same_concept=False,
                        preferred_tag=new_tag,
                        reasoning=f"Network error after {max_retries} retries",
                    )
            except RuntimeError as e:
                # Check if it's a retryable HTTP error
                error_msg = str(e)
                if any(code in error_msg for code in ["429", "500", "502", "503", "504"]):
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"Dedup API error (attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"Dedup API error after {max_retries} attempts: {e}")
                        # Return default decision on final failure
                        return DeduplicationDecision(
                            same_concept=False,
                            preferred_tag=new_tag,
                            reasoning=f"API error after {max_retries} retries",
                        )
                else:
                    # Non-retryable error (400, 401, 404, etc.)
                    logger.warning(f"Dedup API error: {e}, defaulting to no-merge")
                    return DeduplicationDecision(
                        same_concept=False,
                        preferred_tag=new_tag,
                        reasoning="API error, keeping both tags",
                    )

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        user_prompt: str,
        response_schema: dict,
    ) -> DeduplicationDecision:
        """Make the actual API request."""
        async with session.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": DEDUP_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "dedup_decision",
                        "strict": True,
                        "schema": response_schema,
                    },
                },
                "temperature": 0,
                "reasoning": {"enabled": False},
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Dedup API error: {resp.status} {body}")
            data = await resp.json()

        content = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            return DeduplicationDecision.model_validate(parsed)
        except Exception as e:
            logger.warning(f"Failed to parse dedup response: {e}")
            return DeduplicationDecision(
                same_concept=False,
                preferred_tag=new_tag,
                reasoning=f"Parse error: {e}",
            )
