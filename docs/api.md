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
| `GET /v1/repos` | Search and filter: `q`, `country`, `language`, `topic`, `domain`, `tag`, `kind`, `min_stars`, `include_forks`, `include_archived`, `sort` (`stars`, `recently_pushed`, `newest`, `name`), `limit` (max 100), `offset` |
| `GET /v1/repos/{owner}/{name}` | One repo, with its LLM tags and COFOG classification |
| `GET /v1/repos/{owner}/{name}/context` | Snapshot history, government forks and similar repos |
| `GET /v1/overview` | Headline totals, monthly timeline, status breakdown, top countries, newest repos |
| `GET /v1/themes` | Every domain and tag with size, 12-month growth and country spread |
| `GET /v1/themes/{id}` | One domain or tag: monthly new repos, countries, adopters, related tags, top repos |
| `GET /v1/countries` | Repo, account and star counts per country or registry label |
| `GET /v1/taxonomy` | Domains, tags and kinds, with repo counts |
| `GET /v1/stats` | Headline numbers |

CORS is open, so browser apps can call it directly.

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
