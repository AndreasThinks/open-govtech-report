"""Tests for deduplication logic with mocked LLM."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from govtech_scraper.tagger.dedup import TagReconciler
from govtech_scraper.tagger.taxonomy import Taxonomy
from govtech_scraper.tagger.schemas import (
    TaggingResult, TagSuggestion, DeduplicationDecision,
)


class MockEmbeddingProvider:
    """Deterministic embedding provider for testing."""
    
    # Map known tags to fixed embeddings
    KNOWN = {
        "machine-learning": [1.0, 0.0, 0.0],
        "ml": [0.98, 0.05, 0.0],  # very similar to machine-learning
        "web-development": [0.0, 1.0, 0.0],
        "frontend": [0.0, 0.9, 0.3],
        "open-data": [0.0, 0.0, 1.0],
        "data-portal": [0.05, 0.0, 0.95],  # similar to open-data
        "unrelated": [0.5, 0.5, 0.5],
    }

    async def embed(self, texts):
        return [self.KNOWN.get(t, [0.33, 0.33, 0.33]) for t in texts]

    async def embed_single(self, text):
        return self.KNOWN.get(text, [0.33, 0.33, 0.33])


@pytest.fixture
def mock_embeddings():
    return MockEmbeddingProvider()


@pytest.fixture
def taxonomy_with_tags(tmp_db, mock_embeddings):
    taxonomy = Taxonomy(tmp_db)
    # Pre-populate with some tags
    taxonomy.add_tag("machine-learning", embedding=[1.0, 0.0, 0.0])
    for _ in range(4):
        taxonomy.increment_usage("machine-learning")
    taxonomy.add_tag("web-development", embedding=[0.0, 1.0, 0.0])
    taxonomy.add_tag("open-data", embedding=[0.0, 0.0, 1.0])
    return taxonomy


def make_mock_dedup_response(same_concept, preferred_tag, reasoning="test"):
    return {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "same_concept": same_concept,
                    "preferred_tag": preferred_tag,
                    "reasoning": reasoning,
                })
            }
        }]
    }


@pytest.fixture
def reconciler(taxonomy_with_tags, mock_embeddings):
    return TagReconciler(
        taxonomy=taxonomy_with_tags,
        embedding_provider=mock_embeddings,
        api_key="test-key",
        model="test/model",
        similarity_threshold=0.9,
    )


@pytest.mark.asyncio
async def test_exact_match_skips_dedup(taxonomy_with_tags, mock_embeddings):
    """Tags that already exist exactly should not trigger LLM dedup."""
    reconciler = TagReconciler(
        taxonomy=taxonomy_with_tags,
        embedding_provider=mock_embeddings,
        api_key="test-key",
        model="test/model",
    )
    suggestions = TaggingResult(
        tags=[TagSuggestion(tag="machine-learning", confidence=0.9, reasoning="test")],
        primary_category="ml",
    )
    result = await reconciler.reconcile(suggestions, "test repo")
    assert "machine-learning" in result


@pytest.mark.asyncio
async def test_new_tag_added(taxonomy_with_tags, mock_embeddings):
    """Completely new tags should be added to taxonomy."""
    reconciler = TagReconciler(
        taxonomy=taxonomy_with_tags,
        embedding_provider=mock_embeddings,
        api_key="test-key",
        model="test/model",
    )
    suggestions = TaggingResult(
        tags=[TagSuggestion(tag="unrelated", confidence=0.8, reasoning="test")],
        primary_category="other",
    )
    result = await reconciler.reconcile(suggestions, "test repo")
    assert "unrelated" in result
    assert taxonomy_with_tags.has_tag("unrelated")
