"""GitHub App authentication.

Generates JWTs and exchanges them for installation access tokens.
Handles automatic token refresh when expired.
"""

import time
import logging
from typing import Optional

import aiohttp
import jwt

logger = logging.getLogger(__name__)

# GitHub App tokens expire after 1 hour; refresh with 5 min buffer
TOKEN_EXPIRY_BUFFER = 300


class GitHubAuth:
    """Manages GitHub App authentication tokens."""

    def __init__(self, app_id: str, installation_id: str, private_key: str):
        self.app_id = app_id
        self.installation_id = installation_id
        self.private_key = private_key
        self._token: Optional[str] = None
        self._token_expires_at: float = 0

    def _generate_jwt(self) -> str:
        """Generate a JWT for GitHub App authentication."""
        now = int(time.time())
        payload = {
            "iat": now - 60,  # issued at (60s in past for clock drift)
            "exp": now + 600,  # expires in 10 minutes
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def get_token(self, session: aiohttp.ClientSession) -> str:
        """Get a valid installation access token, refreshing if needed."""
        if self._token and time.time() < self._token_expires_at - TOKEN_EXPIRY_BUFFER:
            return self._token

        token_jwt = self._generate_jwt()
        url = f"https://api.github.com/app/installations/{self.installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {token_jwt}",
            "Accept": "application/vnd.github+json",
        }

        async with session.post(url, headers=headers) as resp:
            if resp.status != 201:
                body = await resp.text()
                raise RuntimeError(f"Failed to get installation token: {resp.status} {body}")
            data = await resp.json()

        self._token = data["token"]
        # GitHub tokens expire in 1 hour
        self._token_expires_at = time.time() + 3600
        logger.info("Refreshed GitHub installation access token")
        return self._token

    def get_headers(self, token: str) -> dict[str, str]:
        """Build request headers with auth token."""
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
