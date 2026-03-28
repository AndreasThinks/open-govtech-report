# GovTech Scraper

Scrapes and catalogs public GitHub repositories from government organizations worldwide.

Uses the [government.github.com](https://github.com/github/government.github.com) registry as the source of truth for government GitHub accounts.

## Setup

```bash
cp .env.example .env
# Fill in your GitHub App credentials
uv sync
```

## Usage

```bash
# Full scrape (discover accounts + fetch all repo metadata)
uv run govtech-scraper scrape

# Just refresh the account list
uv run govtech-scraper accounts

# Database stats
uv run govtech-scraper stats

# Options
uv run govtech-scraper scrape --force        # Ignore cache
uv run govtech-scraper scrape --limit 50     # Test with fewer accounts
uv run govtech-scraper -v scrape             # Verbose logging
```

## License

AGPL-3.0
