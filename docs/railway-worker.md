# Railway private worker

The worker is a bounded cron command, separate from metadata scraping and web
serving. Run `python -m govtech_scraper.cloud_worker --help` in the project venv.
Default mode is `dry-run`: verify existing private state, fetch a pinned public
snapshot, merge metadata and report the whole relevant catalogue plan. It never
approves spending, acquires READMEs, posts inference, checkpoints remotely or
publishes. Local metadata/plan state can change.

## State and administration

Private transfers are fixed to the **private dataset**
`AndreasThinks/govtech-private-state`. Every read checks `private=true`, exact
revision, path size and Git/LFS checksum. Checkpoints use SQLite backup, a streaming
hash, integrity/schema validation, parent-SHA guarded commits and exact readback.
The public dataset cannot be configured as the private target.

The parent must prepare a migration seed with these paths:

- `state/master.db`: full authoritative SQLite database, original `ceiling_usd=50`,
  entire billing ledger, receipts, bindings, sources, provenance and review tables.
- `state/review-exclusions.json`: list of objects containing `html_url`,
  `taxonomy_version`, `model`, `tag_id`, `reason`; an empty reviewed list is `[]`.
- `state/manifest.json`, schema below. Hash the closed SQLite backup, not a live
  main file with uncheckpointed WAL.

```json
{
  "schema_version": 1,
  "private_state": true,
  "state_id": "<32 lowercase hexadecimal characters, stable for this lineage>",
  "master": {"bytes": 123, "sha256": "<64 lowercase hexadecimal characters>"},
  "source_publication": "<40-character public dataset commit SHA>",
  "metadata_publication": "<40-character immutable metadata increment SHA>",
  "created_at": "2026-10-14T00:00:00+00:00"
}
```

Bootstrap is explicit and one-time:

```sh
python -m govtech_scraper.cloud_worker --mode bootstrap-only --data-dir /data
python -m govtech_scraper.cloud_worker --mode approve-recurring \
  --authorization-id APPROVED_ID --monthly-usd 5 --increment-usd 2 \
  --approved-at APPROVED_TIME_WITH_OFFSET --effective-at EFFECTIVE_TIME_WITH_OFFSET
```

No ordinary startup environment flag bootstraps, restores or approves authority.
Approval is the existing immutable recurring policy, separate from historical
one-off spending. Administrative commands never infer or publish.

`state/worker-marker.json` is an independently committed, read-back initialization
and dirty-run marker. It contains `schema_version=1`, `initialized=true`, `state_id`,
`dirty`; subsequent runs also record `run_token`, `checkpoint` and `checkpoint_id`.
Worker-local state is in `openrouter_worker_state` and therefore stripped by the
existing public exporter. It includes identity, increment, metadata fingerprint,
cursor, run/checkpoint identities, terminal status and publication/rollout receipts.

Normal mode refuses a missing/corrupt master, missing original ledger/ceiling,
identity drift, a rolled-back clean checkpoint or a foreign dirty run. It does not
construct an empty database first. A nonblocking volume `flock` excludes duplicate
writers; paid database connections use `synchronous=FULL`.

`--mode restore-only` is an administrative import for a **missing** master only.
An existing master must be preserved for operator recovery, never overwritten and
its holds discarded. A restore from a dirty remote lineage is inference-disabled,
even if the backup looks consistent. There is intentionally no command that guesses
refunds, clears this block, renews caps or mass-retries unknown claims. The parent
must reconcile the actual ledger/receipts and review recovery externally. An intact
volume can resume with its existing ledger. The remote dirty marker is installed
before POST and cleared only after a complete ledger checkpoint is verified.

## Configuration and paid execution

Credentials are read explicitly from environment; no `.env`, Hermes credentials,
implicit Hub token or production database is loaded:

| Variable | Purpose |
| --- | --- |
| `GOVTECH_HF_TOKEN` | Explicit private-state and optional publication HF token |
| `GOVTECH_GITHUB_TOKEN` | Public-only GraphQL README acquisition |
| `OPENROUTER_API_KEY` | Paid client, constructed only for classify mode |
| `GOVTECH_INFERENCE_ENABLED=true` | Additional explicit inference gate |
| `RAILWAY_VOLUME_MOUNT_PATH=/data` | Expected durable mount configuration |

```sh
python -m govtech_scraper.cloud_worker --mode classify \
  --authorization-id APPROVED_ID --limit 50 --source-limit 50 \
  --concurrency 2 --deadline 1800 --checkpoint-margin 180
```

Paid mode verifies `/data` in `/proc/self/mountinfo`, not just an environment name.
No ephemeral fallback exists. The route is fixed to `openai/gpt-6-luna`, prompt
`evidence-v2-field-abstention`, the reviewed packaged taxonomy and 900 output tokens.
Injected live model-price lookup must match reviewed input/output prices before
POST; existing strict schema/provider price ceilings and receipt recovery apply.
Reservations atomically bind exact request hash to source URL before network I/O.
Timeout/cancellation keeps both the hold and its known source.

The metadata increment is pinned once per merged metadata content, across crashes
and classification-only publications. A deterministic fingerprint covers original
repository/account/history content, excluding classification and surrogate history
IDs. Whole-catalogue planning and a persistent rotating cursor avoid a repeatedly
selected unchanged or failed prefix. Only current non-fork originals are considered.
Acquisition sends at most `source-limit` requests, with no automatic retries or
REST fanout. Only successfully fetched public README evidence enters the cache;
missing/private/unsupported README responses stay pending. Instructions in README
text are never executed. The core decides meaningful changes and literal-proof
reuse; unchanged failures are retried weekly (`RETRY_FAILED_AFTER`) and abstentions remain assessed.

Concurrency is 1–2, default 2. The async work deadline reserves checkpoint time;
a parent process kills a stuck SDK/thread at the absolute deadline, exiting 124.
After a hard kill the local running status and remote dirty marker may remain;
operator inspection must use the ledger, not interpret missing success as a refund.
Normal completion persists status and checkpoints on failure as well as success.
Status distinguishes planned/reused/acquire/classified/labelled/abstained/failed/
quarantined/budget-exhausted and reports month actual plus holds and increment spend.
Errors printed to stdout are sanitized categories, never provider payloads/tokens.

## Explicit publication and rollout

`--mode classify --publish` opts into publication independently of whether budget
remains. The worker exports a sanitized immutable copy with `allow_partial=True`;
when no fresh assessed evidence exists it may publish metadata only. Existing
freshness and complete metadata coverage gates remain in force. Durable reviewed
exclusions and positive rationales explicitly disclaiming evidence are withdrawn
on the public copy, recorded as review abstentions. Tags also require a literal
quote in their recorded source; private inference rows are never rewritten.
Quote checks decode history envelopes and flat current-cache fields separately.
Missing or malformed proof is withdrawn for review, including non-string field
values or missing repository identity/quoted fields. Malformed history does not
fall back to the current cache. History uses its indexed source hash; fallback
uses the indexed repository URL plus an exact source-hash match, avoiding a
catalogue scan for each tag.

Publication uses existing `publish_bundle` and exact readback, with the dataset
parent as its concurrency guard, independently of code provenance.
New publication manifests require an exact 40-character lowercase hexadecimal
code SHA from `RAILWAY_GIT_COMMIT_SHA` (or explicit `Worker(source_commit=...)`
injection). `GOVTECH_SOURCE_COMMIT` is never a fallback. Missing or invalid code
provenance fails before bundle preparation; dry-run and pending receipt recovery
do not require a new code SHA. An HTTP 409/412
permits at most one fresh metadata fetch/merge/revalidation/export retry, without
another provider call. Other ambiguous failures require an exact remote readback;
unresolved intent is retained and blocks blind replay. Local immutable bundles and
pending intents remain under `/data` for recovery. No automatic pruning is done.

`--publish --rollout` additionally opts into the existing HF Index rollout. A pending
rollout receipt is resumed first; its existing intent/readback safeguards remain.
There is no Railway web rollout adapter in this change. Public metadata workflows
and the reviewed web bootstrap are unchanged.

## Packaging and parent deployment work

Build from repository root with `spaces/railway/Dockerfile.worker`. The dedicated
ignore file allowlists source and lockfiles only. The image pins uv 0.11.7, installs
locked runtime plus API extras without dev dependencies, runs as UID 1000, exposes
no port and defaults to dry-run. Do not configure a daemon/failure restart loop.
The parent must configure the writable UID-1000 `/data` Railway volume, explicit
secrets, cron schedule and restart policy. No secrets belong in the image.

Parent-only remaining work: review migration seed and actual ledger equality;
provision/verify private HF privacy and seed; build the image on a host with Docker;
verify mount ownership, bootstrap/restore, watchdog and restart behavior in staging;
explicitly approve recurring authority; perform an authorized bounded paid smoke;
review and separately implement Railway web rollout/cutover. This implementation
was tested only with synthetic databases and fake service transports.
