"""Government account discovery from github/government.github.com."""

import logging
import sys
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

    logger.info(f"Found {len(accounts)} government accounts across {len(data)} countries")

    # Merge community submissions
    community = _load_community_submissions(seen_usernames)
    if community:
        logger.info(f"Adding {len(community)} community-submitted account(s)")
        accounts.extend(community)

    return accounts


def _load_community_submissions(seen_usernames: set[str]) -> list[GovernmentAccount]:
    """Load validated community submissions from submissions/pending/.

    Skips entries already in the upstream registry.
    Skips and warns on invalid files rather than crashing.
    """
    if not SUBMISSIONS_DIR.exists():
        return []

    submission_files = sorted(SUBMISSIONS_DIR.glob("*.yaml"))
    if not submission_files:
        return []

    # Inline validator import — avoids circular deps and works from any cwd
    scripts_dir = _REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    try:
        from validate_submission import validate_file
    except ImportError:
        logger.warning("Could not import validate_submission — skipping community submissions")
        return []

    added: list[GovernmentAccount] = []
    for path in submission_files:
        if path.name == ".gitkeep":
            continue

        errors = validate_file(path)
        if errors:
            logger.warning(f"Skipping invalid submission {path.name}: {'; '.join(errors)}")
            continue

        with open(path) as f:
            data = yaml.safe_load(f)

        username = str(data["org"]).strip()
        country  = str(data["country"]).strip()
        acct_type = str(data.get("account_type", "org")).strip()

        if username.lower() in seen_usernames:
            logger.debug(f"Submission {path.name}: '{username}' already in upstream registry, skipping")
            continue

        added.append(GovernmentAccount(
            username=username,
            country=country,
            account_type=acct_type,
        ))
        seen_usernames.add(username.lower())
        logger.debug(f"Loaded community submission: {username} ({country}) from {path.name}")

    return added
