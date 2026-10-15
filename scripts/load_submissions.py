#!/usr/bin/env python3
"""
Summarise the accepted submissions in submissions/pending/.

GitHub accounts are merged into the account list by the scrape; accounts on other
forges are merged into the forge host list by the `forges` command. Invalid files
are skipped with a warning.

Usage:
    python scripts/load_submissions.py              # print summary
    python scripts/load_submissions.py --json       # print JSON list for piping
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from govtech_scraper.submissions import load_submissions


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise accepted govtech submissions")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    def warn(path, errors):
        print(f"WARNING: skipping invalid submission {path.name}: {'; '.join(errors)}",
              file=sys.stderr)

    submissions = load_submissions(on_invalid=warn)
    if args.json:
        print(json.dumps([asdict(s) for s in submissions], indent=2))
        return 0
    if not submissions:
        print("No valid submissions found in submissions/pending/")
        return 0

    print(f"Loaded {len(submissions)} community submission(s):\n")
    by_country: dict[str, list] = {}
    for s in submissions:
        name = s.owner if s.is_github else f"{s.host}/{s.owner}"
        by_country.setdefault(s.country, []).append(name)
    for country in sorted(by_country):
        print(f"  {country}: {', '.join(by_country[country])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
