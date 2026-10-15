# Open GovTech Index site

The site that will replace the Streamlit dashboard, at govtechindex.cloud. It is a static,
hash-routed page set in `src/govtech_scraper/site/` (plain HTML, CSS and JavaScript, no build
step) that reads everything from the `/v1` API in [api.md](api.md).

The API app serves the site at `/`, so one free CPU Docker Space (`spaces/api/`) hosts the site,
the REST API and the MCP endpoint. Nothing on the request path calls a model, so running cost
is zero.

## Pages

When the API serves the site, every page has a real path (below) with its own title,
description and link-preview tags, so links can be shared and indexed. Static copies hosted
elsewhere use the same routes after `#` (for example `#/theme/geospatial`); old `#/` links are
moved to paths automatically.

| Path | What it shows |
| --- | --- |
| `/` | Headline numbers, themes treemap (size = repos, colour = pace against the index), repos over time, status breakdown, emerging / cooling / one-team lists with trends, breadth-against-pace chart, countries, newest notable repos |
| `/themes`, `/theme/{id}` | Every COFOG domain and tag; one theme with new repos per month, countries, adopters, teams to talk to, related tags, an RSS feed and repo lists |
| `/countries`, `/country/{id}` | Registry labels grouped by country and level of government; one label's theme mix against the index, active accounts, languages and cross-border reuse |
| `/account/{owner}` | One GitHub account: themes, languages, reuse in both directions, top and recent repos |
| `/repos`, `/repo/{owner}/{name}` | Search (text, country, domain, tag, language, maintained only, forks, archived) with CSV download; one repo's classification, history, forks, README excerpt and badge |
| `/week` | Notable new repos, fastest-growing themes, star movers, archived this week, RSS |
| `/reuse` | Government forks of other governments' code: flows and most reused repos |
| `/compare` | Two labels or two themes side by side |
| `/building-blocks` | Hand-picked components from `api/building_blocks.yaml`, with live figures |
| `/practice` | Licence, description and topic shares by label |
| `/submit` | Builds a `submissions/pending/*.yaml` file and opens it on GitHub |
| `/about`, `/data` | Why the index exists, who runs it, supporters and the method (`/method` redirects); API, MCP, feeds and badges |

Charts have a "Show as a table" option. Feeds live at `/feeds/week.xml`,
`/feeds/theme/{id}.xml` and `/feeds/country/{id}.xml`; badges at `/badge/{owner}/{name}.svg`;
`/sitemap.xml` and `/robots.txt` are generated. Set `GOVTECH_SITE_URL` if the public address is
not `https://www.govtechindex.cloud`.

## Sponsor slot and contact

`config.js` holds `contact` (shown for sponsorship, corrections and submissions) and `sponsor`.
With `sponsor: null` every page shows "Interested in sponsoring it? Get in touch". Set it to
`{name, url, logo, since, blurb}` to credit a sponsor in the strip above the footer, under the
overview's headline numbers and in About's Supporters section.

## Where the numbers come from

- **Themes** are the COFOG domains and taxonomy tags from the decision-model run the API picks
  (see [decision-tagging.md](decision-tagging.md)). Counts cover original (non-fork) repos.
- **Growth** is repos created in the last 12 months as a share of those created before. It
  mostly shows how young a theme is, so the site uses pace for momentum.
- **Pace** is new repos in the last 12 complete months against the 12 before, divided by the
  same ratio for the whole index, with an 80% interval. **Emerging** means the interval is above
  1.0 and at least 5 accounts made the new work, none more than half of it. A rise that fails the
  breadth test is **one team busy** (`concentrated`); an interval below 1.0 is **cooling**.
- **Notable** means a description, some content and a push after the creation day. Lists of new
  repos, the week page and feeds show only notable repos, which hides empty placeholders.
- **Countries** split each registry label into country and level of government
  (`api/places.py`).
- **Reuse** counts forks by one government account of another account's original repo.

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
