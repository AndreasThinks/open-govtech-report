"""Government account discovery from github/government.github.com."""

import logging
from typing import Optional

import aiohttp
import yaml

from .models import GovernmentAccount

logger = logging.getLogger(__name__)

GOVERNMENTS_YAML_URL = (
    "https://raw.githubusercontent.com/github/government.github.com/"
    "gh-pages/_data/governments.yml"
)


async def fetch_government_accounts(
    session: aiohttp.ClientSession,
    url: str = GOVERNMENTS_YAML_URL,
) -> list[GovernmentAccount]:
    """Fetch the list of government GitHub accounts from the YAML registry.

    Returns a flat list of GovernmentAccount objects with country mapping.
    """
    logger.info(f"Fetching government accounts from {url}")
    async with session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Failed to fetch governments YAML: {resp.status}")
        text = await resp.text()

    data = yaml.safe_load(text)
    accounts: list[GovernmentAccount] = []

    for country, usernames in data.items():
        if not isinstance(usernames, list):
            logger.warning(f"Skipping non-list entry for country: {country}")
            continue
        for username in usernames:
            if isinstance(username, str):
                accounts.append(GovernmentAccount(username=username, country=country))
            elif isinstance(username, dict):
                # Some entries are dicts with extra metadata
                name = username.get("name") or username.get("login")
                if name:
                    accounts.append(GovernmentAccount(username=name, country=country))

    logger.info(f"Found {len(accounts)} government accounts across {len(data)} countries")
    return accounts
