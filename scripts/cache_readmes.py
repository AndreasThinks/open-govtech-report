#!/usr/bin/env python3
"""Maintained README source-cache runner.

This wrapper intentionally reads only GITHUB_TOKEN or `gh auth token` in-process.
It does not load a .env file and does not print credentials.
"""

import sys

from govtech_scraper.cli import main


if __name__ == "__main__":
    main(args=["decide", "cache-readmes", *sys.argv[1:]])
