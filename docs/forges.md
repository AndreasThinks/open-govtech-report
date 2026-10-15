# Government code outside GitHub

Some governments host code on their own servers, or on shared ones such as gitlab.com
and Codeberg, rather than GitHub. The `forges` command reads two kinds of source through
each server's public API (no token needed) and stores the projects in the same
`repositories` table:

- **Government servers read in full**, listed by a maintainer in
  `src/govtech_scraper/forge_hosts.yaml` (OpenCoDE, code.overheid.nl, code.europa.eu and
  others).
- **Single accounts on any GitLab or Forgejo/Gitea server**, from accepted community
  submissions in `submissions/pending/` (see `submissions/TEMPLATE.yaml` and the site's
  Add an account page). Submissions on one host are merged into one entry with an
  `owners` list, so a complete run there only touches the submitted accounts.

```bash
uv run govtech-scraper forges                         # every host, plus submissions
uv run govtech-scraper forges --host code.europa.eu   # one host
uv run govtech-scraper forges --limit 20              # test run; marks nothing stale
uv run govtech-scraper forges --no-details            # skip GitLab language and licence calls
uv run govtech-scraper forges --no-submissions        # only forge_hosts.yaml
uv run govtech-scraper forges --allow-host-failures   # exit 0 unless every host fails
```

## Platforms

- **GitLab** (`platform: gitlab`, the default). The project list gives stars, forks,
  topics, archived state and last activity (stored as `pushed_at`, since GitLab has no push
  timestamp). Language, licence and the fork parent (which anonymous project lists leave
  out) cost two extra calls per project; a failed lookup leaves those fields empty.
  Projects with no activity since the last run keep their stored language and licence, so
  only the first run makes every call.
- **Forgejo and Gitea** (`platform: forgejo`), including Codeberg. The repo list already
  has language, size, fork parent and mirror origin, so there are no per-repo calls. It
  has no licence, and `updated_at` stands in for the push date. A whole server is read
  organisation by organisation, so personal accounts are left out.

## Identity and copies

- Owners are stored host-qualified: `gitlab.com/commonground/sub`,
  `code.overheid.nl/MinBZK`. GitHub logins cannot contain dots, so these never collide
  with a GitHub account, and the site and API use them as they are
  (`/v1/repos/gitlab.com/commonground/sub/api`, `/v1/accounts/code.overheid.nl/MinBZK`).
- A repo that copies one already in the index is skipped and counted in the run log.
  Forgejo gives `original_url` for mirrors and migrations, which is matched against every
  indexed URL. A submission can also name `same_as_github`, the same body's GitHub
  login: repos with the same name there are skipped, which is the only check on GitLab
  (it hides the import URL from anonymous callers). On 2026-10-15, 79 of 206 repos on
  code.overheid.nl and 134 of 277 in Codeberg's betagouv were copies of GitHub repos.
- Classification reads GitHub repos only (`CLASSIFIABLE` in `db.py`), because README
  acquisition goes through GitHub's API. Forge repos are counted and searchable but have
  no theme, and are left out of the coverage denominator.

## Running

- Requests are held to 2 a second per server. A 429 pauses that whole server for its
  Retry-After, and a 5xx pauses it for 30 seconds before retrying. A 404 is not retried:
  a submitted account that no longer exists is skipped and reported, and the others are
  still read. Servers are fetched side by side.
- A complete run marks a server's vanished projects `current=0`, as the GitHub scrape
  does per account. It writes no `scrape_runs` row, since publication reads that as
  the GitHub scrape's record.
- The weekly pipeline runs it after the GitHub scrape, so copies of just-refreshed GitHub
  repos are recognised. It has a 150-minute limit and `continue-on-error`: an unreachable
  server never blocks the GitHub data. The first run takes about 90 minutes for the
  GitLab detail calls; later runs reuse them.
- The submission check (`scripts/validate_submission.py --online`, run on submission PRs)
  confirms the account exists through its platform's API.

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

A run from a cloud session on 2026-10-15 read code.overheid.nl (187 repos after empty
and private ones), codeberg.org/betagouv (273) and gitlab.com/commonground (160).

ecosyste.ms was tried first (it indexes all these servers and is free), but it holds
no language, activity or archived data for them.
