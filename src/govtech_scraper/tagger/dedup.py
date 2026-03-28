"""Step 2: Embedding-based tag deduplication and reconciliation."""

import logging
from typing import Optional

import aiohttp

from .schemas import TaggingResult, DeduplicationDecision
from .taxonomy import Taxonomy
from .embeddings import EmbeddingProvider
from .prompts import DEDUP_SYSTEM, DEDUP_USER

logger = logging.getLogger(__name__)


class TagReconciler:
    """Reconciles suggested tags against the existing taxonomy."""

    def __init__(
        self,
        taxonomy: Taxonomy,
        embedding_provider: EmbeddingProvider,
        api_key: str,
        model: str = "google/gemini-flash-1.5",
        base_url: str = "https://openrouter.ai/api/v1",
        similarity_threshold: float = 0.85,
    ):
        self.taxonomy = taxonomy
        self.embeddings = embedding_provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.similarity_threshold = similarity_threshold

    async def reconcile(
        self,
        suggestions: TaggingResult,
        repo_summary: str,
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

        tag_embeddings = await self.embeddings.embed(tag_strings)

        for suggestion, embedding in zip(suggestions.tags, tag_embeddings):
            tag = suggestion.tag

            # 1. Exact match
            if self.taxonomy.has_tag(tag):
                self.taxonomy.increment_usage(tag)
                final_tags.append(tag)
                logger.debug(f"Exact match: {tag}")
                continue

            # 2. Similarity search
            similar = self.taxonomy.find_similar(
                embedding, threshold=self.similarity_threshold
            )

            if similar:
                best_match, best_score = similar[0]
                logger.debug(f"Similar match for '{tag}': '{best_match}' (score={best_score:.3f})")

                # Ask LLM if they're the same concept
                decision = await self._ask_dedup(
                    new_tag=tag,
                    existing_tag=best_match,
                    repo_summary=repo_summary,
                    usage_count=self.taxonomy.get_tag(best_match).usage_count
                    if self.taxonomy.get_tag(best_match)
                    else 0,
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
    ) -> DeduplicationDecision:
        """Ask LLM whether two tags should be merged."""
        import json

        user_prompt = DEDUP_USER.format(
            new_tag=new_tag,
            existing_tag=existing_tag,
            repo_summary=repo_summary,
            usage_count=usage_count,
        )

        response_schema = DeduplicationDecision.model_json_schema()

        async with aiohttp.ClientSession() as session:
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
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"Dedup API error: {resp.status}, defaulting to no-merge")
                    return DeduplicationDecision(
                        same_concept=False,
                        preferred_tag=new_tag,
                        reasoning="API error, keeping both tags",
                    )
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
