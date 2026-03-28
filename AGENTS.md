# GovTech Scraper - Development Guide

## Project Overview

Scrapes and catalogs every public GitHub repository belonging to government organizations worldwide. Source of truth for government accounts is the [government.github.com](https://github.com/github/government.github.com) YAML registry.

## Architecture

```
govtech-scraper/
├── src/
│   └── govtech_scraper/
│       ├── __init__.py
│       ├── cli.py              # CLI entry point (click or argparse)
│       ├── auth.py             # GitHub App JWT/token management
│       ├── accounts.py         # Government account discovery from YAML
│       ├── repos.py            # Repository metadata fetching
│       ├── db.py               # SQLite storage (single source of truth)
│       ├── models.py           # Typed dataclasses for repo metadata
│       ├── retry.py            # Retry/backoff utilities
│       └── export.py           # Parquet/CSV snapshot export
├── tests/
├── pyproject.toml
├── AGENTS.md
└── .env                        # GitHub App credentials (not committed)
```

## Design Principles

- **SQLite is the single source of truth.** No date-stamped files as primary storage. Export is a separate, optional step.
- **ETag/Last-Modified caching lives in the DB.** One store for everything.
- **Stages are independent and composable.** Account discovery, repo metadata fetch, and (future) README fetch can each run alone.
- **Typed dataclasses for all data flowing through the system.** No raw dicts.
- **Retry/backoff is a utility, not copy-pasted.** One decorator, used everywhere.
- **Lean dependencies.** Only what the scraper needs. No ML libs, no web framework, no kitchen sink.

## Auth

Uses GitHub App authentication for 15,000 requests/hour rate limit. Requires:
- `GITHUB_APP_ID`
- `GITHUB_INSTALLATION_ID`
- `GITHUB_PRIVATE_KEY` (PEM format, multiline)

JWT is generated fresh each cycle (10-minute expiry), exchanged for an installation access token (1-hour expiry). Token refresh is automatic and transparent.

## Data Model

Each scrape stores a **snapshot** of repository metadata with a timestamp. This allows tracking changes over time (stars growth, archival, new repos appearing).

Key fields per repo: owner, name, html_url, description, language, stars, forks, watchers, open_issues, size_kb, license, topics, default_branch, fork, archived, created_at, updated_at, pushed_at, commit_count.

Country/org mapping comes from the government.github.com YAML (account -> country).

## Database Schema

- `repositories` — latest state of each repo (upserted on scrape)
- `repository_snapshots` — historical snapshots with scrape_timestamp
- `accounts` — government GitHub accounts with country mapping
- `cache` — ETag/Last-Modified headers keyed by URL

## CLI Usage

```bash
# Full scrape (accounts + repos)
uv run govtech-scraper scrape

# Accounts only (refresh the org list)
uv run govtech-scraper accounts

# Export latest data
uv run govtech-scraper export --format parquet
uv run govtech-scraper export --format csv

# Force full refresh (ignore cache)
uv run govtech-scraper scrape --force

# Limit for testing
uv run govtech-scraper scrape --limit 50
```

## Development

```bash
uv sync                          # Install dependencies
uv run pytest                    # Run tests
uv run govtech-scraper --help    # CLI help
```

## Important Notes

- Always use `uv` for running Python, installing packages, etc.
- GitHub's REST API returns max 100 items per page. Pagination is required.
- Secondary rate limits (abuse detection) need exponential backoff, not just retry.
- The government.github.com YAML nests accounts under country keys. Some entries are orgs, some are individual users. Both have repos.
- GraphQL API is a future optimization target for bulk metadata queries.
- README fetching is a planned second stage, not in initial scope.
