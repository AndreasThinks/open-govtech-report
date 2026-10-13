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
- API up but classification unavailable: repository search and details are live; theme views
  (including their repository lists) use labelled examples. Classification search filters are
  unavailable; see the failure states below.

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

## Failure states and regression checks

Legacy published SQLite files without `repositories.current` are queried read-only: all their
repository rows count as current. When the column exists, `current=0` rows are excluded from
search, details, classification counts and Insights. No schema migration or history rewrite is
performed by the API.

Once the API boots successfully, a repository 404 shows **Not found**. A failed context request
shows unavailable history, government forks and similar repositories; it does not substitute
sample data or report zero forks. A successful, genuinely empty context retains the usual empty
messages. Navigating back to the repository retries the requests.

When the API is live but classification is unavailable, repository search and details stay live.
Domain and tag controls are disabled. Bookmarked searches with a domain, tag or kind show an
explicit unavailable message and no repository rows; **Apply** clears unavailable filters and
searches with the remaining filters. Theme views can still show examples, with their repository
lists explicitly labelled **Example repositories**. Those links resolve against the live API,
so an example repository missing from the live index shows **Not found**. When boot cannot reach
the API, the entire site remains in the banner-labelled sample mode until reload.

Run the dependency-free UI behavioral regressions and API endpoint regressions with:

```bash
node tests/site_behavior.cjs
uv run pytest tests/api
```

The real Chromium suite runs in CI and is optional for local development. It mocks all API requests
and serves only local static files. It
checks desktop (1280px) and mobile (390px) overflow, offline labels, filtered bookmarks, missing
repositories, context errors, navigation recovery, and healthy classified details:

```bash
uv run --with playwright python tests/site_browser.py
```

It requires an installed Playwright Chromium (`uv run --with playwright playwright install chromium`
if needed) and permission to launch a browser and bind a localhost socket. A startup error is not
a passing browser/layout check. Neither suite calls a live model service.

## Metadata rollout operations

The metadata workflow now pins verified dataset revisions into the separately hosted Index.
See [operations.md](operations.md) for receipts, restart recovery, exact operator commands,
and the remaining independent-monitoring and classification boundaries.
