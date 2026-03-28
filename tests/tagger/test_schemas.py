"""Tests for Pydantic schemas."""

import json
import pytest
from pydantic import ValidationError

from govtech_scraper.tagger.schemas import (
    TagSuggestion,
    TaggingResult,
    DeduplicationDecision,
)


def test_tag_suggestion_valid():
    ts = TagSuggestion(tag="open-data", confidence=0.9, reasoning="Has open data portal")
    assert ts.tag == "open-data"
    assert ts.confidence == 0.9


def test_tag_suggestion_confidence_bounds():
    with pytest.raises(ValidationError):
        TagSuggestion(tag="test", confidence=1.5, reasoning="too high")
    with pytest.raises(ValidationError):
        TagSuggestion(tag="test", confidence=-0.1, reasoning="too low")


def test_tagging_result_max_tags():
    tags = [TagSuggestion(tag=f"tag-{i}", confidence=0.5, reasoning="test") for i in range(9)]
    with pytest.raises(ValidationError):
        TaggingResult(tags=tags, primary_category="test")


def test_tagging_result_valid():
    tags = [
        TagSuggestion(tag="data-pipeline", confidence=0.9, reasoning="ETL patterns"),
        TagSuggestion(tag="open-data", confidence=0.85, reasoning="Public datasets"),
    ]
    result = TaggingResult(tags=tags, primary_category="data-management", tech_stack=["python", "pandas"])
    assert len(result.tags) == 2
    assert result.primary_category == "data-management"
    assert "python" in result.tech_stack


def test_tagging_result_serialization():
    tags = [TagSuggestion(tag="test", confidence=0.5, reasoning="test")]
    result = TaggingResult(tags=tags, primary_category="test")
    data = result.model_dump()
    assert isinstance(data, dict)
    json_str = result.model_dump_json()
    assert isinstance(json_str, str)
    # Round-trip
    parsed = TaggingResult.model_validate_json(json_str)
    assert parsed.tags[0].tag == "test"


def test_dedup_decision():
    d = DeduplicationDecision(
        same_concept=True,
        preferred_tag="machine-learning",
        reasoning="ml is an abbreviation"
    )
    assert d.same_concept is True
    assert d.preferred_tag == "machine-learning"
