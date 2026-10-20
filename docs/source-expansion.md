# Reviewed source expansion

This batch uses the existing account-submission and curated-forge routes. It adds
no new scraper framework, schema, classification operation or refresh schedule.

## Accepted account submissions

340 evidence-backed YAML submissions under `submissions/pending/`:

| Country | Accounts/groups |
|---|---:|
| France | 222 |
| Switzerland | 47 |
| Italy | 67 |
| Canada | 4 |

312 are GitHub accounts. The remaining 28 are scoped GitLab groups: 24 on
gitlab.com, three on framagit.org and one on git.wsl.ch. Shared servers are not
crawled wholesale. The Swiss Armed Forces group declares `same_as_github` so
same-named copies of its indexed GitHub projects are skipped.

Every file carries source evidence and account type. Public research and education
accounts are included where the authoritative national registry identifies them;
this is not a claim that every indexed repository is an operational government
service. No private supplier's entire organisation is admitted solely because one
of its products appears in a software catalogue.

### Discovery evidence

- France: [official public-body registry, pinned revision](https://github.com/codegouvfr/codegouvfr-data/blob/992d9953246c164bd749161f383c501d972c8694/comptes-organismes-publics.yml).
  Entries with missing/zero public-body identifiers or ignored markers were not
  used. The source warns it is temporarily unmaintained, so live account checks
  supplement it. Source owner tokens, including case/underscores, are preserved.
- Switzerland: [official federal/cantonal/project owner index](https://github.com/swiss/index)
  and [official OSS catalogue](https://www.opensource.admin.ch/en). The safe group
  explicitly redirects from gitlabext.wsl.ch to the separately verified git.wsl.ch
  host; it is scoped to that group, not the entire research server.
- Italy: [official software catalogue](https://developers.italia.it/en/software),
  [public JSON API](https://api.developers.italia.it/v1/software). Only reviewed
  public-body account identities from the public-administration reuse subset were
  selected. Supplier, personal and ambiguous owners were not batch-submitted.
- Canada: [ORE source YAML](https://github.com/canada-ca/ore-ero/tree/master/_data/code).
  Selected public-body/project accounts have explicit government attribution;
  generic upstream dependencies and personal developers are not treated as
  government organisations.

## Accepted government forge hosts

The existing `forge_hosts.yaml` additionally lists:

- `code.peren.gouv.fr`: French digital-platform regulation expertise.
- `gitlab.cerema.fr`: CEREMA public technical agency.
- `gitlab.has-sante.fr`: Haute Autorité de Santé.
- `gitlab-forge.din.developpement-durable.gouv.fr`: sustainable-development ministry.
- `gitlab.osti.gov`: [US DOE OSTI repository service](https://www.osti.gov/doecode/repository-service).
- `gitlab.inria.fr`: Inria public research; `level: research`.

Personal namespaces remain excluded on every added whole-server entry. The
submission page's existing server list is updated to match. Host metadata and
account scraping use the existing adapters and existing history-preservation
rules. Forge repositories remain outside GitHub-only AI classification.

## Deliberately deferred or excluded

| Source/candidate | Disposition |
|---|---|
| Estonia Koodivaramu | Direct API probes received Cloudflare 403 HTML; no bypass or automatic ingestion. |
| Brazil Software Público | Direct host/forge access blocked; catalogue freshness and current repositories unverified. |
| India OpenForge | DNS resolution failed here; anonymous access and platform compatibility unverified. |
| Grand Lyon forge | API connection timed out here. |
| NZ Common Web Platform | Legacy host did not resolve here. |
| EU federated OSS catalogue | Discovery evidence only; includes third-party software useful to government. Reviewed government owners from national catalogues use normal submissions. |
| State of Public Code | Government-email contributions do not establish government ownership. No bulk import of its archival origins. |
| git.opencode.swiss | Mirror/discovery source. Use canonical Swiss owners instead, not mirror repository copies. |
| France SILL | Recommended third-party software, not a government-owner register. |
| Historical Code.gov data | Archived historical discovery, not a current feed. |
| Australia/NZ national catalogue searches | No additional maintained national code inventory verified in this investigation; existing upstream accounts remain. |
| Generic dependencies/personal or ambiguous supplier accounts | Not submitted, including `ckan`, `ToferC`, `gbetorre`, and unverified whole-owner affiliations. |
| `startupspoleemploi` | Current profile does not corroborate its old registry government identity; not submitted. |
| OW2 LemonLDAP/Sympa groups | Community upstream/mirror groups not submitted as government-owned accounts. |

Seven candidate GitHub identities returned 404 through the account API and were
not submitted: `BCDevExchange`, `CommunicationsSecurityEstablishment`,
`DND-DRDC-RDDC`, `cstb`, `culturecommunication`, `eds-aphp`, and
`semantic-data-for-humanities`. They remain research leads, not silently corrected
or represented as accepted live sources.

## Verification and rollout

- Run the existing validator: `uv run --frozen python scripts/validate_submission.py --all`.
- Run online checks through `submissions.check_online`, using the existing GitHub
  credential path and never printing credentials.
- Full tests: `uv run --frozen pytest tests/ -q`.
- A bounded real metadata smoke test uses a separate disposable SQLite database:
  two public group-owned projects from each of the six new whole-server forges,
  plus GitHub account enumeration in all four added countries. No production DB
  writes or paid classification are needed for these checks.
- The existing submission validator's checkout now fetches full history, because
  its changed-file command needs the PR base and merge history. This is a small
  correctness fix to the existing route, not a new workflow.
- Shared-host retirement is constrained to the enumerated submitted owner paths,
  using literal prefixes so underscores are not SQL wildcards. Missing-group 404s
  retain previous current rows. Regression tests prevent a new group submission
  from retiring unrelated groups already present on the same shared server.

After merge, the unchanged weekly metadata pipeline picks up these submissions
and hosts. A merged source list is not proof of published new repository totals;
that requires completion and readback of the subsequent metadata refresh.
