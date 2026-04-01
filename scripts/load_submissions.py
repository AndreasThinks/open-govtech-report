#!/usr/bin/env python3
"""
Load accepted submissions from submissions/pending/ and print a summary.

This script is called by the scraper pipeline to merge community-submitted
organisations into the account list alongside the upstream governments.yml.

It validates each file before loading — invalid submissions are skipped with
a warning rather than crashing the scrape.

Usage:
    python scripts/load_submissions.py              # print summary
    python scripts/load_submissions.py --json       # print JSON list for piping
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
SUBMISSIONS_DIR = REPO_ROOT / "submissions" / "pending"


def load_all_submissions() -> list[dict]:
    """
    Load and validate all YAML files in submissions/pending/.
    Returns list of dicts with keys: username, country, account_type, description, evidence.
    Skips invalid files with a warning.
    """
    # Lazy import — validator may not be on sys.path in all contexts
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from validate_submission import validate_file

    submissions = []
    skipped = 0

    for path in sorted(SUBMISSIONS_DIR.glob("*.yaml")):
        if path.name == ".gitkeep":
            continue

        errors = validate_file(path)
        if errors:
            print(f"WARNING: skipping invalid submission {path.name}: {'; '.join(errors)}", file=sys.stderr)
            skipped += 1
            continue

        with open(path) as f:
            data = yaml.safe_load(f)

        submissions.append({
            "username": str(data["org"]).strip(),
            "country":  str(data["country"]).strip(),
            "account_type": str(data.get("account_type", "org")).strip(),
            "description": str(data.get("description", "")).strip(),
            "source": "community-submission",
            "submission_file": path.name,
        })

    if skipped:
        print(f"WARNING: {skipped} submission(s) skipped due to validation errors", file=sys.stderr)

    return submissions


def main() -> int:
    parser = argparse.ArgumentParser(description="Load accepted govtech submissions")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    submissions = load_all_submissions()

    if args.json:
        print(json.dumps(submissions, indent=2))
        return 0

    if not submissions:
        print("No valid submissions found in submissions/pending/")
        return 0

    print(f"Loaded {len(submissions)} community submission(s):\n")
    by_country: dict[str, list] = {}
    for s in submissions:
        by_country.setdefault(s["country"], []).append(s["username"])

    for country in sorted(by_country):
        orgs = ", ".join(by_country[country])
        print(f"  {country}: {orgs}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
