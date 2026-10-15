# Railway worker handoff — 2026-10-14

Implemented worker and worker-only packaging from baseline
`535d8438963c52aeba47fc1f45302021b1a06ec6`.
Implementation commit: **`23dfa789f437ec7969646da24e02185487cd16b2`**.
Independent local clone: `/tmp/govtech-railway-worker-verified`, branch
`feat/railway-worker-verified`. The accompanying evidence commit and verified bundle
head are recorded in `railway-worker-commit.txt` (generated after committing).
Bundle: `evidence/railway-worker.bundle` (local export, not recursively committed).

This checkout's linked Git metadata is explicitly read-only. The implementation
was copied into a `git clone --no-hardlinks` independent local clone and committed
there. Original checkout files remain available as working-tree changes. No push,
deployment, DNS, credential lookup, live API, inference or production/private DB
access occurred. Only synthetic test databases and fake transports were used.

## Delivered interfaces

- `cloud_state.HubTransport`: real HF SDK API/download adapter with fixed private
  repository, privacy/exact-revision/path/hash checks, parent guard and readback.
- `cloud_state.PrivateState`: explicit one-time seed import, explicit recovery,
  durable initialization/dirty marker, lineage and checkpoint checks, volume lock,
  `synchronous=FULL`, WAL-safe verified checkpoints and operator-state persistence.
- `cloud_worker.Worker.run`: full-catalogue incremental planning, bounded targeted
  source acquisition, reuse/quarantine, recurring budgets, reviewed price gate,
  fixed route/prompt/taxonomy/900-token cap, status and checkpoint lifecycle.
- `cloud_worker.PublicHub`, `TargetedSources`, `PriceLookup`: runnable real adapters
  tested with injected fake Hub/GitHub/OpenRouter transports.
- `cloud_worker.Worker.publish`: existing sanitized export/publication machinery,
  metadata-only publication when no fresh assessment is available, durable review
  and caveat/quote withdrawals, bounded conflict rebase without provider replay,
  exact ambiguous-commit readback, pending publication/rollout receipts.
- `python -m govtech_scraper.cloud_worker`: defaults to dry-run. Explicit
  `bootstrap-only`, `restore-only`, `approve-recurring`, `classify` modes;
  independent `--publish` and `--rollout` opt-ins; process watchdog exits 124 even
  for a stuck synchronous adapter. The old CLI default model is unchanged.
- Only core change: optional `source_url` in `reserve`/`reserve_or_pending`, bound
  transactionally before POST. Conflicting bindings fail. Timeout/cancellation
  keeps holds and source identity. No historical charges or guessed receipts change.
- `spaces/railway/Dockerfile.worker` and dedicated ignore allowlist: uv **0.11.7**,
  locked runtime + API extras, no dev deps, non-root UID 1000, default dry-run.

Read [worker configuration and recovery documentation](../docs/railway-worker.md)
for exact CLI/environment/manifest fields. Private paths are `state/master.db`,
`state/manifest.json`, `state/review-exclusions.json` and independent
`state/worker-marker.json`. Manifest requires `schema_version=1`,
`private_state=true`, stable 32-hex `state_id`, `master.bytes`, `master.sha256`,
40-hex `source_publication`/`metadata_publication` and aware `created_at`.

Normal mode never restores a missing/corrupt master. Restore refuses to replace
an existing master, protecting uncheckpointed holds. Restoring a remote dirty
lineage is inference-disabled. There is no reset/topup/renewal/recovery bypass.
Local metadata content identity preserves the active increment across restarts
and classification-only public commits; rotating selection avoids prefix starvation.
The public exporter strips every new `openrouter_worker_state` entry along with
existing private ledger/receipt/authorization/binding tables.

## Actual offline verification

All Python runs used checkout-local `.venv`, `UV_PROJECT_ENVIRONMENT="$PWD/.venv"`,
`UV_OFFLINE=1`, `UV_CACHE_DIR=/tmp/govtech-uv` and `uv run --frozen`.
The default uv cache was read-only; switching its cache directory required no
package installation or network bootstrap.

TDD started with the missing-module failure retained in
[red evidence](railway-worker-tdd-red.txt). Subsequent failing regressions exposed
and then verified fixes for source-success decoding, rejected-gate checkpoint
writes, nonliteral-quote withdrawal, stale clean-volume acceptance, unsafe restore
over an existing ledger, and missing per-invocation spend reporting.

- Focused worker + reviewed incremental + recurring budget suites: **47 passed in
  8.75s**. [Output](railway-worker-focused-tests.txt).
- Worker regressions include fake private privacy/revision/hash errors,
  bootstrap/checkpoint/lost-state/dirty restore, intact restart, WAL backup,
  duplicate lock, missing ledger, atomic binding before timeout/cancellation,
  default and rejected paid gates without remote writes, real provider request
  payload/receipt handling, three bounded runs without starvation, stable increment,
  exact quote/caveat exclusions, repeated publication/private-table absence,
  metadata conflict without inference-row changes, actual watchdog termination,
  mounted-volume enforcement and pending rollout resume.
- Unmodified full suite: **timeout exit 124 after 60s**, collected 342 tests at
  that invocation and stalled on `tests/api/test_api.py::test_search_filters_and_pages`.
  No success is claimed. [Output](railway-worker-full-unmodified.txt).
- Final full suite with the pre-existing evidence-only asyncio pipe harness:
  **342 passed, 2 failed, 1 warning in 50.63s**. [Output](railway-worker-full-tests.txt).
  The only failures are `test_http_client_retries_then_succeeds` and
  `test_http_client_raises_on_payment_required`: `PermissionError: [Errno 1]
  Operation not permitted` while constructing local aiohttp server sockets.
  The existing AsyncLimiter loop-reuse warning also remains. This is not a green
  unrestricted host run and the parent must rerun it without the harness.
- `node tests/site_behavior.cjs`: **8 passed, 0 failed**.
  [Output](railway-worker-node-tests.txt).
- CLI `--help` and `git diff --check`: passed.
- Docker executable is present, but local daemon inspection returned permission
  denied on `/var/run/docker.sock`. **No image build/run was performed**; an offline
  build could not be established from this sandbox. No image pull was attempted.

Full harness command (harness is never packaged or imported by production code):

```sh
PYTHONPATH="$PWD/evidence/railway_test_harness" \
UV_CACHE_DIR=/tmp/govtech-uv UV_PROJECT_ENVIRONMENT="$PWD/.venv" UV_OFFLINE=1 \
  timeout 120s uv run --frozen pytest -q
```

## Remaining parent-only deployment work

1. Run unrestricted host tests and build/run the worker image; independently review
   safety, manifest and migration seed. Verify actual private ledger/receipt/hold/
   exclusion equality and the original ceiling without changing old charges.
2. Provision private HF dataset and reviewed seed with the documented schema.
   Verify remote privacy and credentials externally. No seed or production data was
   accessed, generated, uploaded or migrated here.
3. Configure Railway cron, no failure restart loop, real writable UID-1000 `/data`
   volume, environment credentials and explicit authorization ID. Test mount checks,
   one-time bootstrap, checkpoint/restore, lost volume, hard deadline and restart in
   staging before enabling paid work.
4. Explicitly approve recurring authority through the administrative command. Run
   only a separately authorized bounded paid smoke; verify live reviewed prices,
   receipt settlement, monthly/increment accounting and private readback externally.
5. Review explicit publication/rollout operation. Metadata-only publication still
   requires existing complete/fresh metadata coverage. Unsupported/missing README
   acquisitions remain pending; there is no REST fanout or synthetic fallback.
   Retained immutable local bundles need an operator retention policy.
6. Resolve unknown holds/dirty restored state using actual receipts and operator
   recovery; this worker intentionally offers no automatic unblock/refund/reset.
   A hard-killed run may retain `running` status, with its dirty marker and ledger
   as the recovery authority.
7. Railway **web rollout adapter**, staging/canonical verification, external rollout
   and DNS remain separate parent-reviewed work. Existing web/bootstrap files,
   web Docker/tests, metadata workflows and `scripts/update_index.py` were untouched.
