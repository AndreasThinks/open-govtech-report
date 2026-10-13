# Evidence classification build handoff

Branch: `feat/evidence-tagging` at base `3c258d784c2094f1dfb095ded2a2805ade780b8c`.

No credentials, paid APIs, production dataset, publish/deploy/push/merge, DNS, other profiles or
external models were used. No live OpenRouter or GitHub API call was made.

## Implemented

- Added manual evidence-mode classifier at `govtech-scraper decide classify`.
- Added separate SQLite tables:
  - `evidence_sources`
  - `evidence_classifications`
  - `evidence_classification_tags`
  - `openrouter_budget_ledger`
- Preserved legacy probability path (`decision_results` / `decision_probabilities`) and existing
  repository/account/snapshot/tag tables.
- Added strict validation:
  - literal taxonomy IDs only
  - no ID normalization
  - per-label quote must be copied from supplied source and be <=160 chars
  - per-tag rationale required
  - validation failure is stored as a failed classification
- Added source/input fingerprints including model, prompt version, taxonomy digest, source hash,
  metadata hash, README blob SHA and truncation state.
- Added OpenRouter JSON-schema client with fake transport support for tests.
- Added durable budget reservations before external calls, conservative worst-case reservation,
  completed/uncertain states and fail-closed behavior when usage/cost is missing.
- Corrected the local OpenRouter price guard for ordinary Haiku 5.5 calls:
  prompt `$0.10/M`, completion `$0.50/M`, cache write `$0.125/M`, 1h cache write `$0.20/M`,
  cache read `$0.01/M`. The code rejects unknown models, non-finite costs and prompt upper
  bounds at or above 100k tokens instead of entering the premium tier.
- Added provider safeguards to paid requests: `require_parameters`, no fallback, max price
  `$0.10/M` prompt / `$0.50/M` completion, usage included, and OpenRouter reasoning disabled.
- Added durable budget ceiling config, duplicate active request-hash blocking, raw response
  receipts before claim completion, receipt recovery after crash, and fatal accounting breach
  handling when reported actual cost exceeds the reserved amount.
- Added bounded README fetch helper for DB-selected rows. 404/empty/invalid README becomes
  explicit metadata-only provenance; auth/rate-limit failures abort. README text is limited to
  3000 chars and treated as untrusted data by the prompt.
- DB-selected evidence runs now default to current non-fork repos, process bounded batches of 64,
  classify with default concurrency 4, and fetch README sources with default concurrency 8 plus
  roughly 3 GitHub requests/sec pacing. Cached `evidence_sources` are reused on resume when
  metadata and `pushed_at` are unchanged.
- Exposed evidence labels through the shared Catalogue query layer used by REST and MCP:
  - `confidence_mode = evidence`
  - probabilities are `null`
  - repo detail exposes quote/rationale/source_field
  - filters/counts use stored labels, not samples
  - stale metadata fingerprints do not count
  - legacy probability models still work
- Added an explicit offline evidence-publication preparation gate that refuses partial evidence
  publication unless `allow_partial=True`, records coverage in the manifest, and does not perform
  any network publish/deploy action.
- Updated Insights coverage shape for evidence mode:
  - assessed
  - labelled
  - unclassified
  - failed
  - current total
- Updated the static site to avoid calibrated-probability language for evidence mode and show a
  partial-coverage banner.
- Documented pilot, resume, README fetching, partial publication gate and API semantics.

## RED/GREEN evidence

All Python commands used the existing uv-managed environment with
`UV_CACHE_DIR=/tmp/govtech-uv-cache`.

1. RED:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_evidence_classification.py tests/api/test_evidence_api.py -q
   ```

   Result: collection failed with
   `ModuleNotFoundError: No module named 'govtech_scraper.decision.evidence'`.

2. First implementation run:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_evidence_classification.py -q -vv --tb=short
   ```

   Result: `2 failed, 3 passed`. Fixed quote setup in the validation test and the tiny budget
   fixture ceiling.

3. Decision evidence GREEN:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_evidence_classification.py -q
   ```

   Result: `5 passed in 0.55s`.

4. Focused evidence API/query GREEN:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_evidence_classification.py tests/api/test_evidence_api.py -q
   ```

   Result after fixing default model selection for all-stale evidence rows:
   `8 passed in 1.54s`.

4b. Critical billing/throughput RED before the repair:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_evidence_classification.py -q
   ```

   Result: collection failed on the new regression import
   `estimate_prompt_tokens_upper_bound`, confirming the critical tests were not yet implemented.

4c. Critical billing/throughput GREEN:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_evidence_classification.py -q --tb=short
   ```

   Result after fixing pricing/accounting, receipts, schema validation, source caching and
   non-fork defaults: `13 passed in 1.91s`.

4d. Focused API and legacy decision compatibility after the repair:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/api/test_evidence_api.py -q --tb=short
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_decision.py tests/decision/test_quota_guard.py tests/decision/test_tag_controls.py -q -k 'not http_client' --tb=short
   ```

   Result: evidence API `3 passed in 1.24s`; legacy decision `26 passed, 2 deselected in 1.39s`.

4e. Publication gate regression after the repair:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/reliability/test_classification_publication.py -q --tb=short
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/reliability/test_publication.py -q --tb=short
   ```

   Result: classification publication `3 passed in 1.23s`; existing publication `7 passed in 3.16s`.

5. Site behavior after evidence UI changes:

   ```bash
   node tests/site_behavior.cjs
   ```

   First result failed with `ReferenceError: overview is not defined`; fixed to use
   `state.overview`. Final result: `8 passed, 0 failed`.

6. Legacy decision compatibility, avoiding sandbox-prohibited socket tests:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_decision.py tests/decision/test_quota_guard.py tests/decision/test_tag_controls.py -q -k 'not http_client'
   ```

   Result: `26 passed, 2 deselected in 0.88s`.

7. Combined focused verification:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_evidence_classification.py tests/api/test_evidence_api.py -q && node tests/site_behavior.cjs && UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/decision/test_decision.py tests/decision/test_quota_guard.py tests/decision/test_tag_controls.py -q -k 'not http_client'
   ```

   Result: evidence tests `8 passed`, site tests `8 passed`, legacy decision tests
   `26 passed, 2 deselected`.

8. Syntax/whitespace checks:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen python -m compileall -q src
   git diff --check
   ```

   Result: both exit 0.

9. Bounded full-suite attempt:

   ```bash
   timeout 90s env UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/ -q
   ```

   Result: exit 124 after 90 seconds with no completed pytest output before timeout. This is
   consistent with the existing local API/TestClient stall noted in `evidence/OPERATIONS-HANDOFF.md`.

## Known limits

- No real paid pilot was run. The parent still needs to run the reviewed 20-record OpenRouter
  pilot and inspect outputs. Parent must verify live OpenRouter pricing for the exact reviewed
  model before the paid run; this worker made no live API calls.
- The full API `TestClient`/MCP harness still hangs in this sandbox, consistent with the previous
  operational handoff. The new evidence API tests exercise the shared `Catalogue` query layer
  directly. Parent should rerun REST/MCP integration with local socket support.
- The two existing aiohttp HTTP tests fail in this sandbox with
  `PermissionError: [Errno 1] Operation not permitted` when creating sockets. They were not
  changed.
- README-only staleness is detected when the classification source cache is refreshed and a new
  README blob SHA is recorded. Resume reuses cached README evidence when metadata and `pushed_at`
  are unchanged; the read-only API still does not call GitHub to check live README blobs.
- There is no automated publication workflow here. Partial publication remains a parent/operator
  action requiring an explicit gate and review.
- The local OpenRouter price table is deliberately small and fail-closed. If OpenRouter pricing
  changes, update the reviewed price entry before running paid classification. The same SQLite DB
  must carry pilot and bulk ledger state; do not resume against a new empty ledger.

## Operator commands

Dry fixture/pilot:

```bash
export OPENROUTER_API_KEY=...
uv run govtech-scraper --db pilot.db decide classify \
  --inputs evidence/pilot-inputs.jsonl \
  --run-id haiku-pilot-001 \
  --budget-usd 50 \
  --limit 20 \
  --classify-concurrency 4
unset OPENROUTER_API_KEY
```

Resume:

```bash
export OPENROUTER_API_KEY=...
uv run govtech-scraper --db pilot.db decide classify \
  --inputs evidence/pilot-inputs.jsonl \
  --run-id haiku-pilot-001 \
  --budget-usd 50 \
  --classify-concurrency 4
unset OPENROUTER_API_KEY
```

SAME-ledger resume/bulk on current non-fork DB-selected rows with README fetch/cache:

```bash
export OPENROUTER_API_KEY=...
uv run govtech-scraper --db pilot.db decide classify \
  --run-id haiku-bulk-001 \
  --budget-usd 50 \
  --fetch-readmes \
  --batch-size 64 \
  --classify-concurrency 4 \
  --fetch-concurrency 8
unset OPENROUTER_API_KEY
```

Do not publish from this worker. Parent should merge/import onto a fresh reviewed SQLite
baseline, verify source/model/taxonomy hashes, then run the existing publication commands with
the documented partial/full gate.
