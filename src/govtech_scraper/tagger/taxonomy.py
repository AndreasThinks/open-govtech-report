"""Tag vocabulary store and operations.

The Taxonomy is the living vocabulary of tags. It grows incrementally
as repos are tagged, and provides similarity search against existing tags
using cached embeddings.
"""

import json
import logging
import struct
from typing import Optional

from .embeddings import EmbeddingProvider, cosine_similarity

logger = logging.getLogger(__name__)


def _pack_embedding(embedding: list[float]) -> bytes:
    """Pack a float list into bytes for SQLite storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _unpack_embedding(data: bytes) -> list[float]:
    """Unpack bytes back into a float list."""
    count = len(data) // 4  # 4 bytes per float
    return list(struct.unpack(f"{count}f", data))


class TagEntry:
    """In-memory representation of a tag."""
    def __init__(self, tag: str, usage_count: int = 0, embedding: Optional[list[float]] = None):
        self.tag = tag
        self.usage_count = usage_count
        self.embedding = embedding


class Taxonomy:
    """Manages the tag vocabulary with embedding-based similarity search."""

    def __init__(self, db):
        self.db = db
        self._tags: dict[str, TagEntry] = {}
        self._load_from_db()

    def _load_from_db(self) -> None:
        """Load all tags and their embeddings from the database."""
        rows = self.db.conn.execute(
            "SELECT tag, usage_count, embedding FROM tags WHERE merged_into IS NULL"
        ).fetchall()
        for row in rows:
            embedding = _unpack_embedding(row["embedding"]) if row["embedding"] else None
            self._tags[row["tag"]] = TagEntry(
                tag=row["tag"],
                usage_count=row["usage_count"],
                embedding=embedding,
            )
        logger.info(f"Loaded {len(self._tags)} tags into taxonomy")

    def has_tag(self, tag: str) -> bool:
        """Check if a tag exists in the taxonomy."""
        return tag in self._tags

    def get_tag(self, tag: str) -> Optional[TagEntry]:
        return self._tags.get(tag)

    def find_similar(
        self, tag_embedding: list[float], threshold: float = 0.85, top_k: int = 5
    ) -> list[tuple[str, float]]:
        """Find existing tags within cosine similarity threshold.
        
        Returns list of (tag, similarity_score) sorted by similarity descending.
        """
        results = []
        for entry in self._tags.values():
            if entry.embedding is None:
                continue
            sim = cosine_similarity(tag_embedding, entry.embedding)
            if sim >= threshold:
                results.append((entry.tag, sim))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def add_tag(
        self, tag: str, embedding: Optional[list[float]] = None, source_repo: Optional[str] = None
    ) -> None:
        """Add a new tag to the taxonomy."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        
        embedding_blob = _pack_embedding(embedding) if embedding else None
        self.db.conn.execute(
            """INSERT INTO tags (tag, embedding, usage_count, first_seen)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(tag) DO UPDATE SET
                   usage_count = usage_count + 1,
                   embedding = COALESCE(excluded.embedding, tags.embedding)""",
            (tag, embedding_blob, now),
        )
        self.db.conn.commit()

        if tag in self._tags:
            self._tags[tag].usage_count += 1
            if embedding:
                self._tags[tag].embedding = embedding
        else:
            self._tags[tag] = TagEntry(tag=tag, usage_count=1, embedding=embedding)

    def increment_usage(self, tag: str) -> None:
        """Increment usage count for an existing tag."""
        self.db.conn.execute(
            "UPDATE tags SET usage_count = usage_count + 1 WHERE tag = ?", (tag,)
        )
        self.db.conn.commit()
        if tag in self._tags:
            self._tags[tag].usage_count += 1

    def merge_tag(self, old_tag: str, into_tag: str) -> None:
        """Merge one tag into another. Old tag is marked as merged."""
        self.db.conn.execute(
            "UPDATE tags SET merged_into = ? WHERE tag = ?", (into_tag, old_tag)
        )
        # Transfer usage count
        old_entry = self._tags.get(old_tag)
        if old_entry:
            self.db.conn.execute(
                "UPDATE tags SET usage_count = usage_count + ? WHERE tag = ?",
                (old_entry.usage_count, into_tag),
            )
        # Update repository_tags to point to new tag
        self.db.conn.execute(
            "UPDATE OR IGNORE repository_tags SET tag = ? WHERE tag = ?",
            (into_tag, old_tag),
        )
        # Delete any duplicate rows that would violate the primary key
        self.db.conn.execute(
            "DELETE FROM repository_tags WHERE tag = ?", (old_tag,)
        )
        self.db.conn.commit()

        # Update in-memory state
        if old_tag in self._tags:
            if into_tag in self._tags:
                self._tags[into_tag].usage_count += old_entry.usage_count if old_entry else 0
            del self._tags[old_tag]

    def get_top_tags(self, n: int = 50) -> list[str]:
        """Get most-used tags for seeding LLM prompts."""
        sorted_tags = sorted(
            self._tags.values(), key=lambda t: t.usage_count, reverse=True
        )
        return [t.tag for t in sorted_tags[:n]]

    def get_all_tags_with_embeddings(self) -> list[tuple[str, list[float]]]:
        """Return list of (tag_name, embedding) for tags that have embeddings."""
        result = []
        for tag_name, entry in self._tags.items():
            if entry.embedding is not None:
                result.append((tag_name, entry.embedding))
        return result

    @property
    def size(self) -> int:
        return len(self._tags)
