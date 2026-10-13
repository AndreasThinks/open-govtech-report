import asyncio
from unittest.mock import Mock
from govtech_scraper.auth import GitHubAuth


async def test_concurrent_requests_share_one_installation_token_refresh():
    auth = GitHubAuth('1', '2', 'test-key')
    auth._generate_jwt = Mock(return_value='test-jwt')

    class Response:
        status = 201
        async def __aenter__(self):
            await asyncio.sleep(0)
            return self
        async def __aexit__(self, *args):
            pass
        async def json(self):
            return {'token': 'test-token'}

    class Session:
        count = 0
        def post(self, *args, **kwargs):
            self.count += 1
            return Response()

    session = Session()
    results = await asyncio.gather(*(auth.get_token(session) for _ in range(20)))
    assert results == ['test-token'] * 20
    assert session.count == 1
