# Metadata publication and Index rollout

The weekly `scrape-and-publish.yml` workflow is metadata-only. It does not receive
OpenRouter, Jev or decision-service credentials, or invoke tagging/grouping. Existing
legacy tags, classification tables and repository history remain in the SQLite backup.
`run_tagging` is temporarily retained only as a migration guard: `true` fails the job
before restore/scrape; its default is now `false`. `tag_limit` has been removed. Legacy
paid tagging has no scheduled or dispatch path. The CLI remains available for separately
reviewed local experiments; do not use `publish_when_tagged.sh` as a publication runner.
It predates the required baseline receipt and verified snapshot protocol.

All workflows that publish datasets or deploy dashboards use concurrency group
`govtech-publication` with `cancel-in-progress: false`. Future classification publication
must use that same lock. Independent local operators must arrange equivalent exclusive
access. The dataset parent-commit guard also rejects a publisher that restored an older
head. Do not replace its recorded baseline with the latest SHA to bypass a conflict;
restore and scrape again so newer history is retained.

## Publication protocol

`download_db.py` reads the dataset head once, restores
`resolve/<sha>/data/govtech.db` atomically, and records `baseline.json` only after database
validation. `prepare_publication.py` creates a new WAL-safe snapshot directory, preserving
all tables and history and deriving all exports from that snapshot. Existing coverage,
export-integrity and 14-day freshness gates remain mandatory.

`publish_hf.py` commits the entire bundle and dataset card with `parent_commit` equal to
the restored baseline. At the **returned commit SHA**, it checks required paths, sizes,
Git blob hashes or LFS SHA-256 metadata, and downloads and compares the exact manifest.
An incomplete or mismatched readback is a failure even if the upload committed remotely.
Only verified success writes `publication/receipt.json`, containing the dataset SHA,
manifest, artifact metadata, and expected API statistics from the immutable local backup.
The receipt uses current repository counts; the manifest also retains historical totals.

`update_index.py` rechecks that receipt against the same immutable remote revision and
freshness gate. It changes only `GOVTECH_DB_URL` and `GOVTECH_UPDATE_STATE` on
`AndreasThinks/govtech-index`; `GOVTECH_REF`, domain settings, secrets and all other variables
are preserved. The database URL is exactly:

```text
https://huggingface.co/datasets/AndreasThinks/government-github-repos/resolve/<dataset_sha>/data/govtech.db
```

The helper reads back variables, records a durable restart intent, requests one explicit
restart and records completion. The default bounded poll performs 30 attempts, ten seconds
apart, each with a 20-second HTTP timeout. Canonical public
`https://www.govtechindex.cloud/v1/stats` must match `repos`, `last_scraped` and
`classified_repos` from the verified snapshot. Classified counts reflect the API's selected
model and current-row semantics; they are not a certification of model accuracy or fresh
input hashes. Deploy compatible source separately via `GOVTECH_REF` if the currently pinned
API cannot serve those statistics. This workflow does not change the source pin.

The optional native diagnostic uses the original
`https://andreasthinks-govtech-index.hf.space/v1/stats` only when explicitly requested.
Native success after canonical failure still exits nonzero; there is no silent fallback.
HTTP exceptions are suppressed at script boundaries, and tokens are not placed in URLs.

## Operator commands (network writes; run only during an authorized rollout)

From the repository root, with GitHub App credentials for scraping and `HF_TOKEN` in the
environment (never in command arguments):

```bash
uv sync --frozen --dev --extra api --extra decision
uv run --frozen pytest tests/ -q
node tests/site_behavior.cjs
uv run --frozen python scripts/download_db.py
uv run --frozen python scripts/migrate_reliability.py
uv run --frozen govtech-scraper scrape
uv run --frozen python scripts/prepare_publication.py
uv run --frozen python scripts/publish_hf.py --baseline baseline.json
uv run --frozen python scripts/update_index.py --receipt publication/receipt.json
```

Preparation refuses to overwrite an existing publication directory. Keep the previous
bundle and receipt as evidence; use a clean working directory for a new refresh. The
workflow retains the bundle before upload, and baseline/receipt/Index verification files
with `if: always()` after the rollout. The legacy dashboard deployment remains intact.

If only the public health check timed out, rerun just the final helper using the retained
receipt. A `restarted`/`verified` marker for that SHA avoids another restart and rechecks
health. Variable drift fails closed. An `intent` marker means the process may have died
between requesting a restart and recording completion; the helper refuses another restart,
including for a newer dataset. Inspect the Space runtime and provider event history before
operator reconciliation. If restart completion is confirmed, set the marker phase to
`restarted` while retaining its `dataset_sha`, then rerun the helper. Do not clear the marker
blindly. Hugging Face does not provide a transactional restart/idempotency key here; ambiguous
outcomes require this deliberate recovery. Hub-induced rebuilds on configuration changes
are provider behavior; the helper issues at most one explicit restart request per rollout.

To request an explicit native diagnostic while preserving failure of the canonical check:

```bash
uv run --frozen python scripts/update_index.py --receipt publication/receipt.json --diagnose-native-fallback
```

## Monitoring and classification boundaries

Existing script-only dataset freshness/integrity monitoring can be run read-only:

```bash
uv run --frozen python scripts/check_freshness.py --max-age-days 14
```

It exits nonzero on stale, incomplete or inconsistent data. No external cron was provisioned.
The new rollout helper verifies public stats after each publication, but it is **not** an
independent monitor: it can modify the Space. A separate read-only readiness check for
classification input-hash coverage and REST/MCP parity remains a follow-up.

No decision-classification workflow is added in this bounded phase. The parent owns the
sample evaluation, quality decision and budget. Before adding even a manual runner:

- Pin the published dataset baseline SHA and store model/taxonomy/source provenance.
- Retain a WAL-safe checkpoint artifact with `if: always()`, even after scoring failure or
  quota exhaustion. Validate checkpoints and reject resume against a different baseline.
- Keep checkpoint work isolated from newer metadata/history. No untested checkpoint merges;
  reject stale input hashes when repository inputs change.
- Default sample/test runs to no publication. Require explicit publication opt-in plus
  reviewed coverage and quality acceptance; `scored > 0` is insufficient.
- Add no schedule until evaluation and throughput/budget evidence are accepted by the parent.

These are unfinished acceptance criteria, not a claim that checkpoint safety or quality
certification has been implemented. See `evidence/OPERATIONS-HANDOFF.md` for executed checks.
