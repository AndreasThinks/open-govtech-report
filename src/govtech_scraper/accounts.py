"""Government account discovery from github/government.github.com."""

import logging
from pathlib import Path
from typing import Optional

import aiohttp
import yaml

from .models import GovernmentAccount

logger = logging.getLogger(__name__)

GOVERNMENTS_YAML_URL = (
    "https://raw.githubusercontent.com/github/government.github.com/"
    "gh-pages/_data/governments.yml"
)

# Path to community submissions relative to this package
_PACKAGE_ROOT = Path(__file__).parent
_REPO_ROOT = _PACKAGE_ROOT.parent.parent
SUBMISSIONS_DIR = _REPO_ROOT / "submissions" / "pending"


async def fetch_government_accounts(
    session: aiohttp.ClientSession,
    url: str = GOVERNMENTS_YAML_URL,
) -> list[GovernmentAccount]:
    """Fetch government GitHub accounts from the upstream YAML registry plus
    any accepted community submissions in submissions/pending/.

    Returns a deduplicated flat list of GovernmentAccount objects.
    """
    logger.info(f"Fetching government accounts from {url}")
    async with session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Failed to fetch governments YAML: {resp.status}")
        text = await resp.text()

    data = yaml.safe_load(text)
    accounts: list[GovernmentAccount] = []
    seen_usernames: set[str] = set()

    for country, usernames in data.items():
        if not isinstance(usernames, list):
            logger.warning(f"Skipping non-list entry for country: {country}")
            continue
        for username in usernames:
            if isinstance(username, str):
                if username.lower() not in seen_usernames:
                    accounts.append(GovernmentAccount(username=username, country=country))
                    seen_usernames.add(username.lower())
            elif isinstance(username, dict):
                name = username.get("name") or username.get("login")
                if name and name.lower() not in seen_usernames:
                    accounts.append(GovernmentAccount(username=name, country=country))
                    seen_usernames.add(name.lower())

    logger.info("Found %d registry accounts across %d country labels", len(accounts), len({a.country for a in accounts}))

    # Merge community submissions
    community = _load_community_submissions(seen_usernames)
    if community:
        logger.info(f"Adding {len(community)} community-submitted account(s)")
        accounts.extend(community)

    return accounts


def _load_community_submissions(seen_usernames: set[str]) -> list[GovernmentAccount]:
    """Accepted GitHub submissions from submissions/pending/, with registry country labels.

    Skips entries already in the upstream registry, and invalid files with a warning.
    Submissions on other forges are read by the `forges` command instead.
    """
    from .submissions import load_submissions

    def warn(path, errors):
        logger.warning(f"Skipping invalid submission {path.name}: {'; '.join(errors)}")

    added: list[GovernmentAccount] = []
    for sub in load_submissions(SUBMISSIONS_DIR, on_invalid=warn):
        if not sub.is_github:
            continue
        if sub.owner.lower() in seen_usernames:
            logger.debug(f"Submission {sub.file}: '{sub.owner}' already in upstream registry, skipping")
            continue
        added.append(GovernmentAccount(username=sub.owner, country=sub.country,
                                       account_type=sub.account_type))
        seen_usernames.add(sub.owner.lower())
        logger.debug(f"Loaded community submission: {sub.owner} ({sub.country}) from {sub.file}")
    return added
