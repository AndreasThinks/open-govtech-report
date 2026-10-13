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

        # Load existing groups to skip already-named clusters (incremental)
        existing_groups = {
            frozenset(db.get_group_members(row["id"])): (row["name"], row["id"], row["description"])
            for row in db.get_tag_groups()
        }
        logger.info(f"Found {len(existing_groups)} existing tag groups in DB")

        # Name each cluster using LLM — parallel with semaphore
        semaphore = asyncio.Semaphore(5)
        groups: list[dict] = []

        async def _process_cluster(
            key: str, tags: list[str], http_session: aiohttp.ClientSession
        ) -> dict | None:
            # Skip if this exact cluster already exists
            tag_set = frozenset(tags)
            if not recalculate:
                # Reuse names only for exact clusters or strict expansions. Changed
                # concept boundaries require a new name; no automatic tag merging.
                candidates = [(members, info) for members, info in existing_groups.items()
                              if members and members <= tag_set and info[0] != 'Other']
                if len(candidates) == 1:
                    _, (name, group_id, description) = candidates[0]
                    return {"name": name, "description": description, "tags": tags}
                if tag_set in existing_groups:
                    name, _, description = existing_groups[tag_set]
                    return {"name": name, "description": description, "tags": tags}

            if key == "other":
                name = "Other"
                description = "Tags that don't fit neatly into larger categories."
            else:
                async with semaphore:
                    name, description = await self._name_cluster(
                        tags, session=http_session
                    )
            return {"name": name, "description": description, "tags": tags}

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as http_session:
            tasks = [
                _process_cluster(key, tags, http_session)
                for key, tags in final_clusters.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        new_groups = [
            r for r in results if r is not None and not isinstance(r, BaseException)
        ]
        failures = sum(1 for r in results if isinstance(r, BaseException))
        skipped = len(results) - len(new_groups) - failures
        if failures:
            raise RuntimeError(f"{failures} clusters failed to name; existing groups retained")
        logger.info(f"Naming complete: {len(new_groups)} new, {skipped} skipped (already exist)")

        # Replace memberships only after every cluster has a name. One transaction
        # keeps the previous complete grouping if any insert fails.
        with db.conn:
            db.conn.execute('DELETE FROM tag_group_members')
            names = set()
            for group in new_groups:
                name = group['name']
                if name in names:
                    import hashlib
                    name += ' (' + hashlib.sha256(','.join(group['tags']).encode()).hexdigest()[:8] + ')'
                names.add(name)
                from datetime import datetime, timezone
                group_id = db.conn.execute(
                    "INSERT INTO tag_groups(name,description,created_at) VALUES (?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET description=excluded.description RETURNING id",
                    (name, group['description'], datetime.now(timezone.utc).isoformat()),
                ).fetchone()[0]
                db.conn.executemany('INSERT INTO tag_group_members(tag,group_id) VALUES (?,?)',
                                    [(tag, group_id) for tag in group['tags']])
            # Keep empty historical group definitions, but dashboard only shows
            # groups with current memberships in the selected cohort.

        logger.info(f"Saved {len(new_groups)} new tag groups to database")
        # Return all groups (existing + new)
        groups = new_groups
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
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as temp_session:
                        return await self._make_request(
                            temp_session, system_prompt, user_prompt, response_schema
                        )
            except (aiohttp.ClientError, TimeoutError) as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Cluster naming request failed (attempt {attempt + 1}/{max_retries}): {type(e).__name__}. "
                        f"Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Cluster naming failed after {max_retries} attempts: {type(e).__name__}"
                    )
                    # Fallback name
                    raise RuntimeError("Cluster naming failed")
            except RuntimeError as e:
                error_msg = str(e)
                if any(
                    code in error_msg
                    for code in ["429", "500", "502", "503", "504"]
                ):
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"Cluster naming API error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}. "
                            f"Retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"Cluster naming API error after {max_retries} attempts: {type(e).__name__}"
                        )
                        raise RuntimeError("Cluster naming failed")
                else:
                    # Non-retryable error
                    logger.warning(f"Cluster naming API error: {type(e).__name__}, using fallback")
                    raise RuntimeError("Cluster naming failed")

        # Should not reach here, but just in case
        raise RuntimeError("Cluster naming failed")

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict,
    ) -> tuple[str, str]:
        """Make the actual API request to name a cluster."""
        try:
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
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"Cluster naming API error: {resp.status}"
                    )
                data = await resp.json()
        except asyncio.TimeoutError as e:
            raise aiohttp.ClientError(f"Request timed out: {type(e).__name__}") from e

        if not data or not data.get("choices"):
            raise aiohttp.ClientError("API returned empty response")

        content = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            return (parsed["name"], parsed["description"])
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse cluster name response: {type(e).__name__}")
            raise ValueError("Invalid cluster name response") from None
