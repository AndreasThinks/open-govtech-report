# Metadata publication and Index rollout

The weekly `scrape-and-publish.yml` workflow is metadata-only. It does not receive
OpenRouter, Jev or decision-service credentials, or invoke tagging/grouping. Existing
legacy tags, classification tables and repository history remain in the SQLite backup.
Public bundles exclude `openrouter_*` tables and `worker_status`, including their deleted
SQLite pages. Retain the private inference database and immutable checkpoint separately:
they are the authoritative global spending ledger, receipts and quarantine state. Never
resume paid classification against a downloaded public bundle or a fresh ledger.
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

No scheduled decision-classification workflow is added. The parent owns the sample evaluation,
quality decision and live budget. The local `decide classify` runner is manual-only and writes
durable budget/source/evidence tables; before publishing or automating it:

- Pin the published dataset baseline SHA and store model/taxonomy/source provenance.
- Retain a WAL-safe checkpoint artifact with `if: always()`, even after scoring failure or
  quota exhaustion. Validate checkpoints and reject resume against a different baseline.
- Use the same SQLite database for pilot and resumed bulk classification so `openrouter_budget_*`
  keeps the approved global ceiling, completed/uncertain claims and raw response receipts. Do not
  resume against a new empty ledger.
- Before the paid pilot, the parent/operator must verify live OpenRouter pricing for the exact
  reviewed model and keep the persisted ceiling at or below the approved $50 global cap. Unknown
  models and later ceiling increases fail closed.
- Default evidence classification covers current non-fork repositories. Fork coverage requires
  explicit `--include-forks` and must be described separately in coverage reporting.
- Keep checkpoint work isolated from newer metadata/history. No untested checkpoint merges;
  reject stale input hashes when repository inputs change.
- Default sample/test runs to no publication. Require explicit publication opt-in plus
  reviewed coverage and quality acceptance; `scored > 0` is insufficient.
- Partial evidence classification may be published only with an explicit `--allow-partial`
  operator gate and a public coverage banner showing assessed, labelled, unclassified and failed
  rows. Do not describe it as a full-catalogue classification.
- Add no schedule until evaluation and throughput/budget evidence are accepted by the parent.

These are unfinished acceptance criteria, not a claim that checkpoint safety or quality
certification has been implemented. See `evidence/OPERATIONS-HANDOFF.md` for executed checks.

## Evidence recovery worker operations

The recovery worker commands below are manual-only. They must run against the same
`classification.db` and OpenRouter ledger that holds the approved global cap, completed
receipts and held uncertain reservations. Do not copy rows into a fresh database to resume.
Use environment variables for credentials; never pass tokens in command arguments.

Cache public README sources without inference:

```bash
GITHUB_TOKEN=... UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper \
  --db /home/avery/.hermes/reports/govtech-build-20261013/classification.db \
  decide cache-readmes \
  --limit 200 \
  --batch-size 40 \
  --max-requests 10000 \
  --status-path evidence/source-worker-status.json
```

Exit semantics: `0` means completed; `2` means blocked by GitHub auth/permission; `3`
means partial, with unresolved batches left for resume. HTTP 502/503/504, 429 and timeouts
are retried with bounded backoff. Repeated failures split 40-repo batches down toward 20 and
10 and leave unresolved repos waiting; they are not marked as missing README. Private bodies
are skipped and never stored.

Bind the one reviewed historical unknown request to its known source URL without inventing
a generation ID:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper \
  --db /home/avery/.hermes/reports/govtech-build-20261013/classification.db \
  decide bind-pending-source \
  --request-hash 77c2c7f585f5e9f30b8ba8e609b6a7b6bd40981ade625a15a4ac9f7f8a3c7ba9 \
  --source-url https://github.com/npci/ainxt-cli
```

If a pending receipt has an exact generation ID, reconcile its cost by authenticated GET only:

```bash
OPENROUTER_API_KEY=... UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper \
  --db /home/avery/.hermes/reports/govtech-build-20261013/classification.db \
  decide reconcile-generation \
  --request-hash <exact-request-hash>
```

Exit semantics: `0` means reconciled or already reconciled; `3` means the reservation is still
held pending operator review. The reconciler validates generation ID syntax, exact returned ID
match, finite nonnegative documented cost (`data.total_cost` or usage cost), and
`cost <= reservation`. It never sends a replacement POST.

Resume classification only after reviewing pending summaries and live pricing for the exact
model. The reviewed Luna model entry is `openai/gpt-6-luna` at `$0.10/M` prompt and
`$0.50/M` completion in the local reservation table. Keep concurrency modest:

```bash
OPENROUTER_API_KEY=... UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper \
  --db /home/avery/.hermes/reports/govtech-build-20261013/classification.db \
  decide classify \
  --model openai/gpt-6-luna \
  --run-id luna-resume-001 \
  --budget-usd 50 \
  --limit 200 \
  --batch-size 32 \
  --classify-concurrency 4 \
  --fetch-concurrency 8 \
  --prompt-version evidence-v2-field-abstention
```

Exit semantics: `0` means no failed records and no global stop; `1` means one or more records
failed validation after billed responses were preserved; `2` means a global stop such as budget
exhaustion, pricing error, credit/auth failure or accounting breach. Pending reconciliation is
reported separately and is not counted as labelled or assessed.

Inspect durable worker status without trusting stale heartbeats as live processes:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper \
  --db /home/avery/.hermes/reports/govtech-build-20261013/classification.db \
  decide worker-status --worker classifier
```

Exit semantics: `0` for completed or non-terminal readable status, `2` for blocked/failed,
`3` for partial. A delayed heartbeat is historical telemetry only; process liveness must come
from the current process table or supervisor.
