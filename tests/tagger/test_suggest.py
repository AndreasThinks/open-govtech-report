"""Tests for tag suggestion with mocked LLM responses."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiohttp import ClientSession

from govtech_scraper.tagger.suggest import TagSuggester
from govtech_scraper.tagger.schemas import TaggingResult, TagSuggestion


MOCK_LLM_RESPONSE = {
    "choices": [{
        "message": {
            "content": json.dumps({
                "tags": [
                    {"tag": "open-data", "confidence": 0.95, "reasoning": "Government data portal"},
                    {"tag": "data-pipeline", "confidence": 0.8, "reasoning": "ETL processing"},
                ],
                "primary_category": "data-management",
                "tech_stack": ["python", "pandas"],
            })
        }
    }]
}


@pytest.fixture
def suggester():
    return TagSuggester(api_key="test-key", model="test/model")


@pytest.mark.asyncio
async def test_suggest_parses_response(suggester):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=MOCK_LLM_RESPONSE)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock(spec=ClientSession)
    mock_session.post = MagicMock(return_value=mock_resp)

    with patch("aiohttp.ClientSession") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_session)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = MagicMock(return_value=mock_resp)
        mock_cls.return_value = mock_instance

        result = await suggester.suggest("test context")

    assert isinstance(result, TaggingResult)
    assert len(result.tags) == 2
    assert result.tags[0].tag == "open-data"
    assert result.primary_category == "data-management"


def test_fallback_parse(suggester):
    bad_content = 'Some text before {"tags": [{"tag": "test", "confidence": 0.5, "reasoning": "r"}], "primary_category": "cat", "tech_stack": []}'
    result = suggester._fallback_parse(bad_content)
    assert result.tags[0].tag == "test"


def test_fallback_parse_garbage(suggester):
    result = suggester._fallback_parse("completely unparseable garbage")
    assert result.tags == []
    assert result.primary_category == "unknown"
