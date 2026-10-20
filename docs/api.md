# Public API and MCP tools

A read-only REST API and an MCP server over the published database. Both use the same queries
(`src/govtech_scraper/api/queries.py`), so a person and an AI assistant always get the same
answer.

## Run it

```bash
uv sync --extra api
uv run govtech-scraper --db govtech.db serve-api          # http://127.0.0.1:8000, docs at /docs
uv run govtech-scraper --db govtech.db serve-mcp          # MCP over stdio, for Claude Desktop and similar
```

To host it publicly for free, deploy `spaces/api/` as a Hugging Face Docker Space on CPU basic
hardware. When the Space starts, it installs the package from GitHub and downloads the latest
validated database from the public dataset.

```bash
hf repos create AndreasThinks/govtech-api --repo-type space --space-sdk docker
hf upload AndreasThinks/govtech-api spaces/api . --repo-type space
```

The data is only as fresh as the last restart, so restart the Space after each weekly
publication (`HfApi().restart_space(...)`).

## REST

| Endpoint | Returns |
| --- | --- |
| `GET /v1/repos` | Search and filter: `q`, `country`, `language`, `topic`, `domain`, `tag`, `kind`, `min_stars`, `include_forks`, `include_archived`, `owner`, `maintained`, `notable`, `sort` (`stars`, `recently_pushed`, `newest`, `name`), `limit` (max 100), `offset` |
| `GET /v1/repos/{owner}/{name}` | One repo, with its LLM tags, COFOG classification and README excerpt (when published) |
| `GET /v1/repos/{owner}/{name}/context` | Snapshot history, government forks and similar repos |
| `GET /v1/overview` | Headline totals, monthly timeline, status breakdown, top countries, newest notable repos, classification coverage, and repositories by source (GitHub, government servers, submitted accounts on shared servers) |
| `GET /v1/themes` | Every domain and tag with size, 12-month growth, country spread, and momentum: `pace` against the index with an 80% interval (`pace_low`, `pace_high`), accounts and countries behind the new work, the top account's share, a `status` (`emerging`, `concentrated`, `cooling`, `steady`, `too_small`) and a 24-month `trend` |
| `GET /v1/themes/{id}` | One domain or tag: monthly new repos, countries, adopters, teams, related tags, top repos |
| `GET /v1/countries` | Repo, account and star counts per registry label, with `slug`, `country_name` and `level` |
| `GET /v1/places` | Every registry label with country, level of government, maintained repos and licence share |
| `GET /v1/countries/{slug}` | One label: theme mix against the index, top accounts, languages, reuse both ways, repos |
| `GET /v1/accounts/{owner}` | One GitHub account: themes, languages, reuse both ways, top and recent repos |
| `GET /v1/reuse` | Government forks of other governments' code: flows between labels, most reused repos |
| `GET /v1/week` | Latest scrape: notable new repos, archived repos, star movers, fastest-growing themes |
| `GET /v1/practice` | Licence, description, topic and archive shares by label |
| `GET /v1/building-blocks` | Hand-picked components with live figures |
| `GET /v1/taxonomy` | Domains, tags and kinds, with repo counts |
| `GET /v1/stats` | Headline numbers |

CORS is open, so browser apps can call it directly. The same app also serves RSS feeds
(`/feeds/week.xml`, `/feeds/theme/{id}.xml`, `/feeds/country/{slug}.xml`), README badges
(`/badge/{owner}/{name}.svg`), `/sitemap.xml` and the site's page paths; see [site.md](site.md).

## MCP

The MCP server runs at `/mcp/` (streamable HTTP, stateless, JSON responses) and offers these
tools: `search_repositories`, `get_repository`, `list_countries`, `get_taxonomy`,
`get_statistics`, `list_themes` and `get_theme`.

The same app serves the Open GovTech Index site at `/` (see [site.md](site.md)); the old JSON
index moved to `/api`.

Example client configuration:

```json
{ "mcpServers": { "govtech": { "url": "https://<space-subdomain>.hf.space/mcp/" } } }
```

## Classification data

The `domain`, `tag` and `kind` filters need decision-tagging results in the database (see
[decision-tagging.md](decision-tagging.md)). Without them, those filters return HTTP 400 and
everything else still works. If more than one decision run is stored, the API uses the run that
covers the most repos for the current taxonomy version. Set `DECISION_MODEL` to choose a
different one.

Evidence-backed runs are exposed through the same REST and MCP surfaces as legacy decision runs,
but `confidence_mode` is `evidence` and probabilities are `null`. Repository detail responses
include per-tag `quote`, `rationale` and `source_field` provenance. Legacy numeric probability
models continue to work.

Evidence-mode aggregate counts include only current repository rows whose stored metadata
fingerprint still matches the catalogue row. README freshness is tied to the recorded README blob
SHA at classification time; a README-only change is detected when the classification source cache
is refreshed, not by the read-only API.
