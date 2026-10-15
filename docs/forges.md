# Government GitLab servers outside GitHub (experimental)

Some governments host code on their own GitLab servers rather than GitHub. The
`forges` command reads each server's public GitLab API (no token needed) and stores
the projects in the same `repositories` table. Their `html_url` points at the server,
so they never collide with GitHub rows.

```bash
uv run govtech-scraper forges                         # every host in forge_hosts.yaml
uv run govtech-scraper forges --host code.europa.eu   # one host
uv run govtech-scraper forges --limit 20              # test run; marks nothing stale
uv run govtech-scraper forges --no-details            # skip language and licence calls
```

- Hosts live in `src/govtech_scraper/forge_hosts.yaml`, with the same country labels
  as the government.github.com registry. `owners:` limits a shared server such as
  gitlab.com to named groups; `include_personal: true` keeps projects in personal
  namespaces, which are skipped by default.
- The project list gives stars, forks, topics, archived state and last activity
  (stored as `pushed_at`, since GitLab has no push timestamp). Language, licence and
  the fork parent (which anonymous project lists leave out) cost two extra calls per
  project; a failed lookup leaves those fields empty.
- Requests are held to 2 a second per server. A 429 pauses that whole server for its
  Retry-After, and a 5xx pauses it for 30 seconds before retrying. Servers are fetched side by side. Projects with no activity since the last
  run keep their stored language and licence, so only the first run makes every call.
- A complete run marks a server's vanished projects `current=0`, as the GitHub scrape
  does per account. It writes no `scrape_runs` row, since publication reads that as
  the GitHub scrape's record.
- The weekly pipeline does not run it yet, so published data and the site have no
  forge rows.

## Live test (2026-10-15, from GitHub Actions)

| Server | Repos (personal skipped) | Active in last year |
|---|---|---|
| repo1.dso.mil | 4,849 | 2,390 |
| gitlab.opencode.de | 3,216 | 2,490 |
| code.europa.eu | 2,091 | 2,029 |
| code.usgs.gov | 1,049 | 475 |

Language was found for 80% and licence for 79%; few projects have a description (23%)
or topics (5%). DoD's server is mostly Iron Bank container packaging (Dockerfile is its
top language), and OpenCoDE includes some test repos. Two servers did not answer:

- gitlab.adullact.net times out from GitHub's runners (it answers ecosyste.ms).
- code.ornl.gov listed 805 projects in one run, then returned 504 on its first page
  in two later runs.

A failing server is reported and skipped; the others are still saved.

ecosyste.ms was tried first (it indexes all these servers and is free), but it holds
no language, activity or archived data for them.
