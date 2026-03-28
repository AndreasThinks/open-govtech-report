"""Hierarchical tag grouping using embedding clustering + LLM naming."""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Optional

import aiohttp
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage

from .taxonomy import Taxonomy
from ..db import Database

logger = logging.getLogger(__name__)


class TagGrouper:
    """Clusters tag embeddings and names the clusters using an LLM."""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen/qwen3-32b",
        base_url: str = "https://openrouter.ai/api/v1",
        min_cluster_size: int = 3,
        distance_threshold: float = 1.0,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.min_cluster_size = min_cluster_size
        self.distance_threshold = distance_threshold

    async def build_groups(
        self,
        taxonomy: Taxonomy,
        db: Database,
        session: Optional[aiohttp.ClientSession] = None,
        recalculate: bool = False,
    ) -> list[dict]:
        """Build hierarchical tag groups from taxonomy embeddings.

        Args:
            taxonomy: Taxonomy instance with loaded tags
            db: Database instance for persistence
            session: Optional aiohttp session for reuse
            recalculate: If True, clear existing groups first

        Returns:
            List of dicts with keys: name, description, tags
        """
        if recalculate:
            db.clear_tag_groups()

        # Get all tags with embeddings
        tags_with_embeddings = taxonomy.get_all_tags_with_embeddings()
        logger.info(f"Found {len(tags_with_embeddings)} tags with embeddings")

        if len(tags_with_embeddings) < self.min_cluster_size:
            logger.warning(
                f"Not enough tags with embeddings ({len(tags_with_embeddings)}) "
                f"for clustering (min={self.min_cluster_size})"
            )
            return []

        # Build numpy array of embeddings
        tag_names = [t[0] for t in tags_with_embeddings]
        embeddings = np.array([t[1] for t in tags_with_embeddings])

        # Hierarchical clustering
        Z = linkage(embeddings, method="ward")
        labels = fcluster(Z, t=self.distance_threshold, criterion="distance")

        # Group tags by cluster label
        clusters: dict[int, list[str]] = defaultdict(list)
        for tag_name, label in zip(tag_names, labels):
            clusters[label].append(tag_name)

        # Merge small clusters into "Other"
        final_clusters: dict[str, list[str]] = {}
        other_tags: list[str] = []
        cluster_id = 0

        for label, tags in clusters.items():
            if len(tags) < self.min_cluster_size:
                other_tags.extend(tags)
            else:
                final_clusters[f"cluster_{cluster_id}"] = sorted(tags)
                cluster_id += 1

        if other_tags:
            final_clusters["other"] = sorted(other_tags)

        logger.info(
            f"Created {len(final_clusters)} clusters "
            f"({cluster_id} named + {'1 other' if other_tags else '0 other'})"
        )

        # Name each cluster using LLM
        groups: list[dict] = []

        async def _process_cluster(key: str, tags: list[str]) -> dict:
            if key == "other":
                name = "Other"
                description = "Tags that don't fit neatly into larger categories."
            else:
                name, description = await self._name_cluster(tags, session=session)
            return {"name": name, "description": description, "tags": tags}

        if session:
            for key, tags in final_clusters.items():
                group = await _process_cluster(key, tags)
                groups.append(group)
        else:
            async with aiohttp.ClientSession() as temp_session:
                for key, tags in final_clusters.items():
                    group = await _process_cluster(key, tags)
                    groups.append(group)

        # Save to database
        for group in groups:
            group_id = db.save_tag_group(group["name"], group["description"])
            for tag in group["tags"]:
                db.save_tag_group_member(tag, group_id)

        logger.info(f"Saved {len(groups)} tag groups to database")
        return groups

    async def _name_cluster(
        self, tags: list[str], session: Optional[aiohttp.ClientSession] = None
    ) -> tuple[str, str]:
        """Ask LLM to name a cluster of tags.

        Args:
            tags: List of tag strings in the cluster
            session: Optional aiohttp session for reuse

        Returns:
            Tuple of (group_name, description)
        """
        system_prompt = (
            "You are a taxonomy expert. Given a list of tags from government "
            "GitHub repositories, suggest a concise group name (2-4 words, "
            "title case) and a one-sentence description for the category "
            "they belong to."
        )
        user_prompt = f"Tags: {', '.join(tags)}"

        response_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["name", "description"],
            "additionalProperties": False,
        }

        # Retry logic with exponential backoff
        max_retries = 3
        base_delay = 2.0

        for attempt in range(max_retries):
            try:
                if session:
                    return await self._make_request(
                        session, system_prompt, user_prompt, response_schema
                    )
                else:
                    async with aiohttp.ClientSession() as temp_session:
                        return await self._make_request(
                            temp_session, system_prompt, user_prompt, response_schema
                        )
            except aiohttp.ClientError as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Cluster naming request failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Cluster naming failed after {max_retries} attempts: {e}"
                    )
                    # Fallback name
                    return ("Unnamed Group", "Failed to generate group name.")
            except RuntimeError as e:
                error_msg = str(e)
                if any(
                    code in error_msg
                    for code in ["429", "500", "502", "503", "504"]
                ):
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"Cluster naming API error (attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"Cluster naming API error after {max_retries} attempts: {e}"
                        )
                        return ("Unnamed Group", "Failed to generate group name.")
                else:
                    # Non-retryable error
                    logger.warning(f"Cluster naming API error: {e}, using fallback")
                    return ("Unnamed Group", "Failed to generate group name.")

        # Should not reach here, but just in case
        return ("Unnamed Group", "Failed to generate group name.")

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict,
    ) -> tuple[str, str]:
        """Make the actual API request to name a cluster."""
        async with session.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "cluster_name",
                        "strict": True,
                        "schema": response_schema,
                    },
                },
                "temperature": 0,
                "reasoning": {"enabled": False},
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Cluster naming API error: {resp.status} {body}"
                )
            data = await resp.json()

        content = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            return (parsed["name"], parsed["description"])
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse cluster name response: {e}")
            return ("Unnamed Group", "Failed to parse group name.")
