# GovTech Scraper

Scrapes and catalogs every public GitHub repository belonging to government organizations worldwide, then uses an LLM-powered tagging pipeline to automatically categorize them.

**🏛️ [Live Dashboard](https://huggingface.co/spaces/AndreasThinks/govtech-dashboard)** — explore the data interactively.
**📦 [Dataset on Hugging Face](https://huggingface.co/datasets/AndreasThinks/government-github-repos)** — download the full SQLite DB, CSV, and Parquet exports.

Uses the [government.github.com](https://github.com/github/government.github.com) registry as the source of truth for government GitHub accounts, supplemented by [community submissions](submissions/pending/).

**Know a government org that's missing?** See [CONTRIBUTING.md](CONTRIBUTING.md) — open a PR with a YAML file and it'll be included in the next scrape.

## What it does

1. **Discovers** government GitHub accounts from the global registry (~2000+ orgs across 100+ countries)
2. **Scrapes** repository metadata via the GitHub API (stars, forks, language, license, topics, etc.)
3. **Tags** repositories using an agentic LLM pipeline with structured output and embedding-based deduplication
4. **Stores** everything in SQLite with historical snapshots for tracking changes over time

## Architecture

```
src/govtech_scraper/
├── cli.py              # CLI entry point (click)
├── auth.py             # GitHub App JWT/token management
├── accounts.py         # Government account discovery from YAML
├── repos.py            # Async repository metadata fetching
├── db.py               # SQLite storage (single source of truth)
├── models.py           # Typed dataclasses
├── retry.py            # Retry/backoff utilities
├── export.py           # Parquet/CSV snapshot export
└── tagger/             # Agentic tagging pipeline
    ├── schemas.py      # Pydantic models for structured LLM output
    ├── prompts.py      # Prompt templates
    ├── context.py      # Token-budgeted context builder
    ├── suggest.py      # LLM tag suggestion (OpenRouter)
    ├── dedup.py        # Embedding similarity + LLM merge decisions
    ├── taxonomy.py     # Living tag vocabulary with cached vectors
    └── embeddings.py   # Embedding provider abstraction
```

## Setup

```bash
# Clone and install
git clone https://github.com/AndreasThinks/open-govtech-report.git
cd open-govtech-report
cp .env.example .env
# Fill in your credentials (see below)
uv sync
```

### Credentials

**GitHub App** (required for scraping) — create one at github.com/settings/apps with read-only repository contents and metadata permissions:

```
GITHUB_APP_ID=your_app_id
GITHUB_INSTALLATION_ID=your_installation_id
GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----"
```

**OpenRouter** (required for tagging):

```
OPENROUTER_API_KEY=your_key
```

## Usage

```bash
# Scrape all government repos
uv run govtech-scraper scrape

# Just refresh the account list
uv run govtech-scraper accounts

# Tag repos using LLM + embedding deduplication
uv run govtech-scraper tag

# Test with a small batch
uv run govtech-scraper tag --limit 10

# Re-tag everything
uv run govtech-scraper tag --retag

# Use a different model
uv run govtech-scraper tag --model google/gemini-2.0-flash-001

# Database stats (repos, languages, tags)
uv run govtech-scraper stats

# Options
uv run govtech-scraper scrape --force        # Ignore cache
uv run govtech-scraper scrape --limit 50     # Test with fewer accounts
uv run govtech-scraper -v scrape             # Verbose logging
```

## Tagging Pipeline

The tagger uses a two-step approach to build a clean, deduplicated taxonomy:

1. **Suggest** — An LLM reads repo metadata, README, and (when available) dependency manifests, file trees, and entry point code. It returns structured tags via OpenRouter with a ~2000 token context budget per repo.

2. **Deduplicate** — Each suggested tag is embedded and compared against the existing taxonomy via cosine similarity. When a close match is found (>0.85), a lightweight LLM call decides whether to merge ("is 'ml' the same as 'machine-learning'?") or keep both.

The first 50 repos seed the taxonomy freely (cold start). After that, deduplication kicks in to prevent drift.

## Data

All data lives in a single SQLite database (`govtech.db`). Key tables:

- `repositories` — latest state of each repo (upserted on scrape)
- `repository_snapshots` — historical snapshots with timestamps
- `accounts` — government GitHub accounts with country mapping
- `tags` — tag vocabulary with cached embeddings
- `repository_tags` — repo-tag associations with confidence scores

Export to flat files when needed:

```bash
uv run govtech-scraper export --format csv
uv run govtech-scraper export --format parquet
```

## License

AGPL-3.0
