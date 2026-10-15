# Government forges outside GitHub (experimental)

Some governments host code on their own GitLab servers rather than GitHub. The
`forges` command pulls those repositories through [ecosyste.ms](https://repos.ecosyste.ms),
which already indexes them, and stores them in the same `repositories` table.
Their `html_url` points at the forge, so they never collide with GitHub rows.

```bash
uv run govtech-scraper forges                         # every host in forge_hosts.yaml
uv run govtech-scraper forges --host code.europa.eu   # one host
uv run govtech-scraper forges --limit 20              # test run; marks nothing stale
```

- Hosts live in `src/govtech_scraper/forge_hosts.yaml`, with the same country labels
  as the government.github.com registry. `owners:` limits a shared host such as
  gitlab.com to named groups.
- Mirrors of other repositories and records ecosyste.ms marks as gone are skipped.
- A complete run marks a host's vanished repos `current=0`, as the GitHub scrape does
  per account. It writes no `scrape_runs` row, since publication reads that as the
  GitHub scrape's record.
- Free tier: 300 anonymous requests an hour; the client stays under 280. One page is
  100 repos.
- **Licence:** ecosyste.ms data is CC BY-SA 4.0. Exports that include these rows need
  attribution and share-alike.
- Not yet part of the weekly pipeline, the site copy, or the exports.
