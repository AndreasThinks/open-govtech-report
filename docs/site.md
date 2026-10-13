# Open GovTech Index site

The site that will replace the Streamlit dashboard, at govtechindex.cloud. It is a static,
hash-routed page set in `src/govtech_scraper/site/` (plain HTML, CSS and JavaScript, no build
step) that reads everything from the `/v1` API in [api.md](api.md).

The API app serves the site at `/`, so one free CPU Docker Space (`spaces/api/`) hosts the site,
the REST API and the MCP endpoint. Nothing on the request path calls a model, so running cost
is zero.

## Pages

| Route | What it shows |
| --- | --- |
| `#/` | Headline numbers, themes treemap (size = repos, colour = 12-month growth), repos over time, status breakdown, momentum scatter, fastest-growing themes, countries, newest repos |
| `#/themes` | Every COFOG domain with its tags |
| `#/theme/{id}` | One domain or tag: new repos per month, countries, adopting accounts, how the model assigns it, related tags, most starred and newest repos. "Track this theme" saves to the browser |
| `#/repos` | Search and filter by text, country, domain, tag, forks and archived |
| `#/repo/{owner}/{name}` | Classification probabilities, stars and forks history, government forks, similar repos |
| `#/method`, `#/data` | How the numbers are made; API and MCP details |

## Where the numbers come from

- **Themes** are the COFOG domains and taxonomy tags from the decision-model run the API picks
  (see [decision-tagging.md](decision-tagging.md)). Counts cover original (non-fork) repos.
- **Growth** is repos created in the last 12 months as a share of those created before. It
  works from a single scrape; once snapshots build up it can move to snapshot deltas.
- **Emerging** means at least five repos, smaller than three quarters of tags, and growing at
  least twice as fast as the whole index (and at least 20%).

## Sample-data fallback

- API unreachable: every view uses `sample.js`, with a banner saying so.
- API up but no decision run stored: repo figures are live; theme views use sample themes,
  with a banner saying so.

## Hosting elsewhere

To put the site on GitHub Pages or Cloudflare Pages instead, copy `src/govtech_scraper/site/`
and set `api` in `config.js` to the API Space's URL. CORS is already open.

## Local preview

```bash
uv sync --extra api
curl -L -o govtech.db https://huggingface.co/datasets/AndreasThinks/government-github-repos/resolve/main/data/govtech.db
uv run govtech-scraper --db govtech.db serve-api
# open http://127.0.0.1:8000/
```
