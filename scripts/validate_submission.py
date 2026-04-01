#!/usr/bin/env python3
"""
Validate a submission YAML file (or all files in submissions/pending/).

Usage:
    python scripts/validate_submission.py submissions/pending/gb-nhs-digital.yaml
    python scripts/validate_submission.py --all

Exit 0 = all valid. Exit 1 = validation errors found.
Called by the validate-submission GitHub Actions workflow on PRs.
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
SUBMISSIONS_DIR = REPO_ROOT / "submissions" / "pending"

REQUIRED_FIELDS = ["org", "country", "account_type", "description", "evidence"]
VALID_ACCOUNT_TYPES = {"org", "user"}

# ISO 3166-1 alpha-2 codes (the ones most likely to appear; non-exhaustive)
# A full hard-coded list would be maintenance burden — we do a format check instead
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

# Basic sanity check for a GitHub org/user login
ORG_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,37}[a-zA-Z0-9])?$")


def validate_file(path: Path) -> list[str]:
    """Validate a single submission file. Returns list of error strings (empty = valid)."""
    errors = []

    # Must be YAML-parseable
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]

    if not isinstance(data, dict):
        return ["File must contain a YAML mapping (key: value pairs)"]

    # Required fields present
    for field in REQUIRED_FIELDS:
        if field not in data or data[field] is None or data[field] == "":
            errors.append(f"Missing required field: '{field}'")

    if errors:
        return errors  # stop here — further checks depend on fields being present

    # org: valid GitHub login format
    org = str(data["org"]).strip()
    if not ORG_RE.match(org):
        errors.append(
            f"'org' value '{org}' doesn't look like a valid GitHub login "
            "(alphanumeric and hyphens only, no leading/trailing hyphens, max 39 chars)"
        )

    # country: ISO 3166-1 alpha-2
    country = str(data["country"]).strip()
    if not COUNTRY_RE.match(country):
        errors.append(
            f"'country' value '{country}' must be a 2-letter ISO 3166-1 alpha-2 code (e.g. GB, US, DE)"
        )

    # account_type: org or user
    if data["account_type"] not in VALID_ACCOUNT_TYPES:
        errors.append(
            f"'account_type' must be 'org' or 'user', got '{data['account_type']}'"
        )

    # description: non-empty string
    desc = str(data.get("description", "")).strip()
    if not desc or desc == "Short description of the organisation":
        errors.append("'description' must be a non-empty, non-placeholder string")

    # evidence: list with at least one non-empty entry
    evidence = data.get("evidence", [])
    if not isinstance(evidence, list) or not any(
        isinstance(e, str) and e.strip() and not e.startswith("http://example")
        for e in evidence
    ):
        errors.append(
            "'evidence' must be a list with at least one real URL linking to an "
            "authoritative source confirming government status"
        )

    # Filename convention: should match <country>-<something>.yaml
    stem = path.stem.lower()
    expected_prefix = country.lower() + "-"
    if not stem.startswith(expected_prefix):
        errors.append(
            f"Filename '{path.name}' should start with the country code prefix '{expected_prefix}' "
            f"(e.g. '{expected_prefix}{org.lower()}.yaml')"
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate govtech submission YAML files")
    parser.add_argument("files", nargs="*", type=Path, help="Submission files to validate")
    parser.add_argument("--all", action="store_true", help="Validate all files in submissions/pending/")
    args = parser.parse_args()

    if args.all:
        targets = sorted(SUBMISSIONS_DIR.glob("*.yaml"))
        if not targets:
            print("No submission files found in submissions/pending/")
            return 0
    elif args.files:
        targets = args.files
    else:
        parser.print_help()
        return 1

    all_valid = True
    for path in targets:
        errors = validate_file(path)
        if errors:
            all_valid = False
            print(f"\nFAIL: {path.name}")
            for err in errors:
                print(f"  ✗ {err}")
        else:
            print(f"OK:   {path.name}")

    if not all_valid:
        print(f"\nValidation failed. Fix the errors above and re-push.")
        return 1

    print(f"\nAll {len(targets)} submission(s) valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
