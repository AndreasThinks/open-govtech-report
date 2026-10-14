# Worker recovery handoff

Branch: `feat/evidence-tagging`. No push, merge, deploy, publish, production DB mutation,
credential use, live GitHub API call, live OpenRouter call, or paid inference was performed.
The production `classification.db` was not opened or modified by this worker.

## Implemented

- Moved README source acquisition into maintained code:
  `src/govtech_scraper/decision/source_worker.py` plus `scripts/cache_readmes.py` and
  `govtech-scraper decide cache-readmes`.
- Source worker behavior:
  - uses reusable GraphQL README query/decoder and `EvidenceStore` cache helpers
  - retries HTTP 502/503/504/429/timeouts with bounded backoff
  - splits repeated 40-repo failures toward 20 and 10
  - leaves unresolved batches waiting instead of marking them missing
  - commits each repo source immediately after successful decode
  - resumes from unchanged cache, not numeric offset alone
  - skips private/inaccessible README bodies
  - writes structured JSON status on completion, partial failure and auth block
- Provider receipt recovery:
  - every returned provider body, including HTTP/provider errors and missing-cost responses,
    is saved before billing interpretation
  - receipts store request hash, claim id, model, source URL, input hash, generation id when
    present, HTTP status, sanitized response JSON and usage JSON
  - authorization/cookie/API-key headers are not persisted
  - classifier accepts per-call response receipts and no longer depends on a shared
    `last_response` identity for fake/custom transports
  - malformed billed JSON remains a completed billed failure; no first-object parsing or quote
    repair was added
- Billing and quarantine:
  - added `PricingError`, `AccountingBreach`, `UnreconciledBilling` and `PendingRequest`
  - existing reserved/uncertain request hashes are treated as per-record pending and skipped
    without a new POST or whole-run halt
  - accounting breach, unknown price and actual cost above reservation remain global fatal
  - pending reservations stay counted by `pending_reconciliation_summary()`
- Reconciliation:
  - added `src/govtech_scraper/decision/reconcile.py`
  - authenticated GET `/api/v1/generation?id=<exact-id>` is supported by fake-tested transport
  - validates generation ID syntax, exact returned ID, finite nonnegative documented cost and
    cost no greater than reservation
  - no automatic replacement POST is part of reconciliation
- Operator CLIs:
  - `decide cache-readmes`
  - `decide reconcile-generation`
  - `decide bind-pending-source`
  - `decide worker-status`
- Durable worker status:
  - `WorkerStatusStore` records phase, run id, model, pending reconciliation count, committed
    counts, final error, heartbeat/start/finish timestamps
  - local exclusive lock prevents duplicate paid workers in the same DB
  - exception paths write terminal failed status in `finally`

## RED/GREEN evidence

All commands used the existing isolated environment and `UV_CACHE_DIR=/tmp/uv-cache` because
the default uv cache path is read-only in this sandbox.

Initial genuine RED:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen pytest \
  tests/decision/test_source_worker_recovery.py \
  tests/decision/test_provider_receipt_recovery.py \
  tests/decision/test_billing_reconciliation.py \
  tests/decision/test_uncertain_request_quarantine.py \
  tests/decision/test_worker_status.py -q
```

Result: collection failed with missing `source_worker`, `reconcile`, `ProviderResponseError`,
`AccountingBreach` and `WorkerStatusStore`.

Focused recovery GREEN:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen pytest \
  tests/decision/test_source_worker_recovery.py \
  tests/decision/test_provider_receipt_recovery.py \
  tests/decision/test_billing_reconciliation.py \
  tests/decision/test_uncertain_request_quarantine.py \
  tests/decision/test_worker_status.py -q
```

Result: `22 passed in 1.08s`.

Focused recovery plus neighboring decision compatibility:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen pytest \
  tests/decision/test_source_worker_recovery.py \
  tests/decision/test_provider_receipt_recovery.py \
  tests/decision/test_billing_reconciliation.py \
  tests/decision/test_uncertain_request_quarantine.py \
  tests/decision/test_worker_status.py \
  tests/decision/test_evidence_classification.py \
  tests/decision/test_readme_graphql.py \
  tests/decision/test_field_abstention.py \
  tests/decision/test_luna_route.py \
  tests/decision/test_quota_guard.py -q
```

Result: `54 passed in 1.76s`.

Site behavior:

```bash
node tests/site_behavior.cjs
```

Result: `8 passed, 0 failed`.

Syntax/whitespace:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen python -m compileall -q src scripts
git diff --check
```

Result: both exit `0`.

CLI help smoke checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper decide cache-readmes --help
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper decide reconcile-generation --help
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper decide bind-pending-source --help
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper decide worker-status --help
```

Result: all exit `0` and show the expected command help.

Full-suite attempt:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen pytest -q
```

Result: no pytest output after more than four minutes; interrupted with Ctrl-C, exit `130`.
This is not a full-suite pass. Parent should rerun the full suite on the host/CI.

## Operator commands and exit semantics

Source cache runner:

```bash
GITHUB_TOKEN=... UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper \
  --db /home/avery/.hermes/reports/govtech-build-20261013/classification.db \
  decide cache-readmes \
  --limit 200 \
  --batch-size 40 \
  --max-requests 10000 \
  --status-path evidence/source-worker-status.json
```

Exits: `0` completed, `2` auth/permission blocked, `3` partial/unresolved waiting.

Historical unknown claim 1276 binding, no generation ID guessed:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper \
  --db /home/avery/.hermes/reports/govtech-build-20261013/classification.db \
  decide bind-pending-source \
  --request-hash 77c2c7f585f5e9f30b8ba8e609b6a7b6bd40981ade625a15a4ac9f7f8a3c7ba9 \
  --source-url https://github.com/npci/ainxt-cli
```

Generation-cost reconciliation for records with exact saved generation IDs:

```bash
OPENROUTER_API_KEY=... UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper \
  --db /home/avery/.hermes/reports/govtech-build-20261013/classification.db \
  decide reconcile-generation \
  --request-hash <exact-request-hash>
```

Exits: `0` reconciled/already reconciled, `3` still held/quarantined.

Cached-ready classification resume, bounded 200-row lane:

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

Exits: `0` no failed records/global stop, `1` failed records after saved receipts, `2`
global stop such as budget exhaustion, pricing error, auth/credit failure or accounting breach.

Bulk resume uses the same command without `--limit 200` only after parent review of the
bounded lane and pending/quarantine summaries.

Worker status:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen govtech-scraper \
  --db /home/avery/.hermes/reports/govtech-build-20261013/classification.db \
  decide worker-status --worker classifier
```

Exits: `0` readable non-error/completed status, `2` blocked/failed, `3` partial.

## Preserved factual boundaries

- Claim 1276 remains quarantined/reserved. It is associated only with exact hash
  `77c2c7f585f5e9f30b8ba8e609b6a7b6bd40981ade625a15a4ac9f7f8a3c7ba9` and reviewed URL
  `https://github.com/npci/ainxt-cli`. No generation ID or classification outcome was invented.
- The seven additional uncertain Haiku reservations remain counted. No uncertain refund was
  implemented.
- The `$50` global ceiling is still a persisted ledger ceiling; increases are rejected.
- No production resume, source cache run, generation lookup, or paid classification was claimed.
