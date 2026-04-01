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

## Tagger Module

The `tagger/` package implements an agentic tagging pipeline:

### Architecture
```
tagger/
├── __init__.py      # Public API: tag_repo(), tag_batch()
├── schemas.py       # Pydantic models for structured LLM output
├── prompts.py       # Prompt templates (separate from logic)
├── context.py       # Builds LLM context within token budget
├── suggest.py       # Step 1: LLM tag suggestion via OpenRouter
├── dedup.py         # Step 2: Embedding similarity + LLM merge decision
├── taxonomy.py      # Living tag vocabulary with embedding cache
└── embeddings.py    # Embedding provider abstraction
```

### Pipeline
1. **Context building** — assembles repo metadata, README, file tree, dependency manifest, entry point code within a ~2000 token budget
2. **Tag suggestion** — sends context to LLM (default: Gemini Flash via OpenRouter) with structured output, gets back tags + category + tech stack
3. **Deduplication** — embeds suggested tags, finds similar existing tags via cosine similarity, asks LLM "are these the same concept?" to decide merge vs keep
4. **Cold start** — first 50 repos skip dedup and seed the taxonomy freely

### DB Tables
- `tags` — tag vocabulary with cached embeddings and usage counts
- `repository_tags` — many-to-many repo-tag associations with confidence and source

### CLI
```bash
uv run govtech-scraper tag              # tag all untagged repos
uv run govtech-scraper tag --limit 50   # test run
uv run govtech-scraper tag --retag      # re-tag everything
uv run govtech-scraper tag --model google/gemini-2.0-flash-001  # use different model
```

### Extractability
The tagger/ package only depends on the parent project through the `Repository` dataclass and `Database` for reading/storing. To spin out as a library, swap those for generic interfaces.

### Environment
Requires `OPENROUTER_API_KEY` in .env for both LLM calls and embeddings.

## Dashboard (HuggingFace Space)

The `web/` directory contains a Streamlit dashboard for exploring the dataset, deployed as a HuggingFace Space.

### Architecture
```
web/
├── app.py              # Streamlit app (single-file, multi-tab)
└── requirements.txt    # Streamlit, plotly, pandas, huggingface-hub
```

### Pages
1. **Overview** — headline metrics, country distribution bar chart, language distribution, creation timeline
2. **Explorer** — filterable, searchable, paginated repo table with country/language/stars/archived/fork filters
3. **Tags** — top tags bar chart, tag groups with expandable members, browse repos by tag. Shows "tagging in progress" banner when <50% tagged
4. **Insights** — top 50 starred repos, license breakdown pie, fork vs original ratio, most recently active repos, language×country heatmap

### Data Source
The app looks for `govtech.db` in:
1. `../govtech.db` (parent directory — for local dev)
2. `./govtech.db` (current directory)
3. Downloads from HuggingFace dataset `AndreasThinks/government-github-repos` (for Space deployment)

All queries use `@st.cache_data(ttl=300)` for performance.

### Local Development
```bash
cd web
uv run streamlit run app.py
```

### HuggingFace Spaces Deployment
- SDK: Streamlit
- Hardware: Free tier (no GPU needed)
- The app auto-downloads the DB from the HF dataset repo on startup
- Weekly pipeline (GitHub Actions) pushes updated DB → Space auto-refreshes on next visit

## Known Bugs / To-Do

- ~~**`json` NameError in dedup**~~ — Fixed: `import json` moved to module top-level in `dedup.py`.
- ~~**`list index out of range` in reconcile phase**~~ — Fixed: fallback in parse error handler now uses `new_tag` directly instead of fragile `user_prompt.split("'")[1]`.

## Important Notes

- Always use `uv` for running Python, installing packages, etc.
- GitHub's REST API returns max 100 items per page. Pagination is required.
- Secondary rate limits (abuse detection) need exponential backoff, not just retry.
- The government.github.com YAML nests accounts under country keys. Some entries are orgs, some are individual users. Both have repos.
- GraphQL API is a future optimization target for bulk metadata queries.
- README fetching is a planned second stage, not in initial scope.
