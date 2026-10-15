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
  (stored as `pushed_at`, since GitLab has no push timestamp). Language and licence
  cost one extra call each per project; a failed lookup leaves the field empty.
- Requests are held to 2 a second per server, and a 429 pauses that whole server for its
  Retry-After. Servers are fetched side by side. Projects with no activity since the last
  run keep their stored language and licence, so only the first run makes every call.
- A complete run marks a server's vanished projects `current=0`, as the GitHub scrape
  does per account. It writes no `scrape_runs` row, since publication reads that as
  the GitHub scrape's record.
- The weekly pipeline does not run it yet, so published data and the site have no
  forge rows.

ecosyste.ms was tried first (it indexes all these servers and is free), but it holds
no language, activity or archived data for them.
