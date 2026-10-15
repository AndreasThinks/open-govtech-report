#!/usr/bin/env python3
"""
Validate a submission YAML file (or all files in submissions/pending/).

Usage:
    python scripts/validate_submission.py submissions/pending/nl-commonground.yaml
    python scripts/validate_submission.py --all
    python scripts/validate_submission.py --online <files>   # also ask the forge's API

Exit 0 = all valid. Exit 1 = validation errors found.
Called by the validate-submission GitHub Actions workflow on PRs. The rules live in
govtech_scraper.submissions, which the scraper also uses to load submissions.
"""

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from govtech_scraper.submissions import (
    SUBMISSIONS_DIR,
    check_online,
    parse,
    validate_file,
)

__all__ = ["validate_file"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate govtech submission YAML files")
    parser.add_argument("files", nargs="*", type=Path, help="Submission files to validate")
    parser.add_argument("--all", action="store_true",
                        help="Validate all files in submissions/pending/")
    parser.add_argument("--online", action="store_true",
                        help="Also confirm each account exists through its platform's API")
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
        errors, warnings = validate_file(path), []
        if not errors and args.online:
            errors, warnings = check_online(parse(yaml.safe_load(path.read_text()), path.name))
        for warning in warnings:
            print(f"WARN: {path.name}: {warning}")
        if errors:
            all_valid = False
            print(f"\nFAIL: {path.name}")
            for err in errors:
                print(f"  ✗ {err}")
        else:
            print(f"OK:   {path.name}")

    if not all_valid:
        print("\nValidation failed. Fix the errors above and re-push.")
        return 1

    print(f"\nAll {len(targets)} submission(s) valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
