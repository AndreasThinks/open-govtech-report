import pytest
from govtech_scraper.tagger.dedup import TagReconciler


async def test_malformed_dedup_response_fails_instead_of_accepting_new_tag():
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def json(self):
            return {'choices': [{'message': {'content': 'not valid JSON'}}]}
    class Session:
        def post(self, *args, **kwargs):
            return Response()
    reconciler = TagReconciler(taxonomy=None, embedding_provider=None, api_key='test-key')
    with pytest.raises(ValueError, match='Invalid deduplication response'):
        await reconciler._make_request(Session(), 'test', {})
