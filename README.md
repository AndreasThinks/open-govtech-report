# GovTech Scraper

Catalogs public GitHub repositories owned by accounts in the government.github.com registry and accepted community submissions, then uses an LLM-powered tagging pipeline to automatically categorize them.

**🏛️ [Live Dashboard](https://huggingface.co/spaces/AndreasThinks/govtech-dashboard)** — explore the data interactively.
**📦 [Dataset on Hugging Face](https://huggingface.co/datasets/AndreasThinks/government-github-repos)** — download the full SQLite DB, CSV, and Parquet exports.

Uses the [government.github.com](https://github.com/github/government.github.com) registry as the source of truth for government GitHub accounts, supplemented by [community submissions](submissions/pending/).

**Know a government org that's missing?** See [CONTRIBUTING.md](CONTRIBUTING.md) — open a PR with a YAML file and it'll be included in the next scrape.

## What it does

1. **Discovers** government GitHub accounts from the upstream registry; counts are measured per run, not a worldwide census
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
uv sync --frozen
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
# Scrape repositories for registry accounts
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

1. **Suggest** — Batch tagging uses repository metadata only. README caching/fetching is not implemented; `get_readme` returns `None`, and batch calls do not supply manifests, file trees or entry points. The lower-level `tag_repo` API can accept caller-supplied context. It returns structured tags via OpenRouter with a ~2000 token context budget per repo.

2. **Deduplicate** — Each suggested tag is embedded and compared against the existing taxonomy via cosine similarity. When a close match is found (>0.85), a lightweight LLM call decides whether to merge ("is 'ml' the same as 'machine-learning'?") or keep both.

Cold start lasts until the concept taxonomy contains 50 tags (not 50 repositories). After that, deduplication kicks in to prevent drift.

## Experimental: decision-model tagging and public API

- **Decision-model tagging** scores repos against a fixed COFOG taxonomy using an open-weight Kev model (or hosted Jev), with probabilities instead of generated tags. See [docs/decision-tagging.md](docs/decision-tagging.md).
- **Public API and MCP tools**: read-only REST endpoints and an MCP server over the dataset. See [docs/api.md](docs/api.md).

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

## Reliability and recovery

The weekly pipeline restores history before scraping. A failed restore is fatal; it
never silently bootstraps an empty replacement. Initial creation is an explicit local
operation (`govtech-scraper --db new.db scrape`), separate from the publication workflow.
Do not restore over a database with live connections or WAL sidecars.

`repositories` retains previously observed repositories, including ones no longer
returned. `current=0` is set only after complete enumeration of that account; a 404
retains prior rows. `repository_snapshots` is append-only. `scrape_runs` and
`account_fetches` retain succeeded/unchanged/not-found/failed coverage. Any unresolved
fetch error fails the CLI and publication gate. Limited test scrapes are not complete
coverage. Authentication errors, timeouts, and later-page 404s are failures.
First-page conditional caching is deliberately disabled: an ETag on one page cannot
prove that the entire account enumeration is unchanged.

Run the idempotent offline migration on a backup/copy before updating legacy data:

```bash
uv run --frozen python scripts/migrate_reliability.py --db copied.db
```

The migration registers normalized technology labels without embeddings or paid calls.
Original normalized-away labels and confidences remain in `tag_label_history`;
meaningful concepts are not automatically merged. Historical language labels remain
classified as `language`, and technology labels as `technology`. Embedding grouping
uses `concept` labels only. Usage counts are recalculated from actual links. Existing
snapshots are preserved verbatim. Unmeasured legacy `commit_count=0` becomes NULL in
latest-state rows; historical zero values also mean unknown, not measured zero commits.
New unknown commit counts and unknown fork parents are NULL; no per-repo API calls are
made to fill these fields.

Tagging failures (including credit exhaustion) exit nonzero while retaining saved work.
Empty metadata repositories are intentionally skipped and reported separately.
`group` updates clusters for new concept tags and reuses names for exact clusters or
strict expansions. `group --recalculate` explicitly requests fresh naming; the weekly
pipeline does not use it, avoiding unnecessary paid calls. Failed naming leaves the
previous complete grouping intact.

Prepare one immutable publication directory (the directory must not already exist):

```bash
uv run --frozen pytest tests/ -q
uv run --frozen python scripts/prepare_publication.py --db govtech.db --output publication
# Only an authorized publisher with HF_TOKEN runs this next command:
uv run --frozen python scripts/publish_hf.py
```

SQLite's backup API captures WAL data and history. All CSV/Parquet/group exports come
from that snapshot; integrity, schema, links, counts, every repository value and URL,
and SHA-256 checksums are checked before a single HF commit. Missing exports fail the
publication; old remote Parquet files cannot silently survive a successful update.
`data/manifest.json` separates source commit, maximum repository scrape timestamp,
publication timestamp and refresh coverage. A maximum scrape timestamp alone does not
prove full coverage. No automatic cleanup deletes historical snapshots.

Pipeline and dashboard deployments share a concurrency group with cancellation disabled.
Tests must pass before either write. The dashboard's selected tags mean **any selected
tag**; one EXISTS filter defines the cohort across charts. Explorer text search is local
to Explorer. Tag-group browsing loads one selected group, and capped lists label both
displayed and total counts. Set `GOVTECH_DB_PATH` to use an offline fixture/copy.

GitHub may disable scheduled workflows in inactive public repositories (60 days without
repository activity). A green old run is not proof of freshness. An independent scheduler
outside GitHub should execute this read-only check daily and alert on any nonzero exit:

```bash
uv run --frozen python scripts/check_freshness.py --max-age-days 14
```

It checks an immutable remote revision, manifest freshness, complete coverage, and
artifact metadata. Do not use empty commits as a keepalive. To recover: re-enable the
Scrape & Publish workflow in GitHub Actions, verify credentials, manually dispatch a
full run, inspect coverage and retained artifacts, then run the independent check and
confirm dashboard publication/scrape dates. These are operator steps, not actions taken
by the repair tests. Never bypass a failed restore, test, export or coverage gate.

Offline tests mock only network/provider boundaries and use temporary SQLite fixtures.
Streamlit AppTest covers default/search/archived, tag combinations, other sidebar filters,
empty results and pagination. Production DBs and paid providers are not needed.

## License

AGPL-3.0
