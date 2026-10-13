from unittest.mock import AsyncMock
import pytest
from govtech_scraper.tagger.dedup import TagReconciler
from govtech_scraper.tagger.taxonomy import Taxonomy


async def test_dedup_credit_failure_propagates(tmp_db):
    reconciler = TagReconciler(Taxonomy(tmp_db), AsyncMock(), 'offline')
    reconciler._make_request = AsyncMock(side_effect=RuntimeError('HTTP 402'))
    with pytest.raises(RuntimeError):
        await reconciler._ask_dedup('new', 'existing', 'summary', 1, session=object())


def test_unparseable_suggestion_is_failure():
    from govtech_scraper.tagger.suggest import TagSuggester
    with pytest.raises(ValueError):
        TagSuggester('offline')._fallback_parse('invalid provider output')
