# Operational fixes handoff

Bounded phase completed locally on `fix/index-operations`, starting at
`8d052527f9245cfeeab8c312048aabe5292b06ac`. No commit or push was made. No dataset publication,
Space restart, paid call, live inference, DNS/domain/helper-repository action, or external
cron setup was performed. All Hub interactions in new tests use injected fakes. Work stops
at points 1–3, as permitted by the request; points 4–5 are explicitly unfinished below.

## Changes

- Weekly metadata workflow no longer contains paid tag/group steps or provider credentials.
  Default `run_tagging=false`; an explicit `true` fails before work instead of being ignored.
  Removed `tag_limit`. Migration and operator commands are in `docs/operations.md`.
- Shared `govtech-publication` concurrency remains non-cancelling. All workflows running the
  full suite install `--extra api`, including the standalone legacy dashboard deployment.
- Restore resolves the dataset head once and downloads its immutable database, preserving
  existing atomic restore validation. Successful restore records `baseline.json`.
- Publication requires that baseline as `parent_commit`, preserves the card and every
  existing bundle artifact/history table, and verifies the exact returned SHA. Required
  artifact paths/sizes and Git/LFS hashes plus downloaded manifest contents must match.
  A mismatch produces no success receipt. Only success atomically writes `receipt.json`.
- New `govtech_scraper.operations` and `scripts/update_index.py` reverify publication, set
  only `GOVTECH_DB_URL` and a dedicated restart-state variable, and retain `GOVTECH_REF`,
  domains and other variables. The URL pins `resolve/<sha>/data/govtech.db`.
- The helper persists and reads back restart intent before issuing one explicit restart.
  Successful request state permits health-check retries without another restart. Ambiguous
  intent and variable drift fail closed for operator review. This is deliberately not a
  claim of transactional exactly-once semantics from the Hub API.
- Bounded canonical `/v1/stats` checks compare current repositories, last scrape timestamp
  and classification count against the verified SQLite snapshot. Native HF diagnostics are
  opt-in and cannot turn a canonical failure into success. Errors omit transport details.
- Workflow keeps the pre-publication bundle and always retains baseline/receipt/Index result.
  Legacy dashboard deployment code is intact and runs after a verified publication even if
  the new Index health verification fails. A failed publication never triggers deployment.
- No dependency lock changes.

## RED/GREEN evidence

All Python commands used the existing uv-managed environment. Executed pytest commands below
were prefixed with `UV_CACHE_DIR=/tmp/govtech-uv-cache` to avoid the read-only default cache.

1. Initial new test run had one actual workflow failure and six fixture setup errors
   (`account_fetches.repo_count` omitted). Corrected the fixture before implementation.
2. `uv run --frozen pytest tests/reliability/test_index_operations.py -q` then produced
   **8 failed in 3.87s** against the original implementation. Two demonstrate actual unsafe
   behavior: scheduled workflow contains OpenRouter credentials, and the original publisher
   printed `Validated bundle published` when the fake remote had no artifacts. The remaining
   failures exposed the absent operations API.
3. After initial fixes, the same command produced **8 passed in 2.49s**.
4. Added immutable-restore regression before changing restore. Its isolated run failed:
   the original URL contained `/resolve/main/` instead of the resolved dataset SHA.
5. After restore/guard integration, focused operations + monitor/publisher + regressions
   produced **21 passed in 5.49s**.
6. Expanded operations tests for LFS readback, manifest mismatch, parent conflict, variable
   readback, explicit native diagnosis, bounded polling, restart retry and real SQLite
   classification/current/history/tag semantics: **15 passed in 3.38s**.
7. Added a workflow regression for legacy deployment after failed Index health; it failed
   before adding an explicit publication outcome condition. Fixed the condition.
8. Final focused command:

   ```bash
   UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/reliability/test_index_operations.py tests/reliability/test_monitor_publish.py tests/reliability/test_workflows.py tests/reliability/test_regressions.py -q
   ```

   Result: **31 passed in 7.03s**, exit 0.
9. `node tests/site_behavior.cjs`: **8 passed, 0 failed**, exit 0.
10. `git diff --check`: exit 0, no whitespace errors (repeated after final edits).

## Full-suite limits: parent must rerun

- `UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/ -q` stalled before the
  first test result; interrupted with Ctrl-C, exit 130.
- Diagnostic attempt:

  ```bash
  timeout 90s env UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/ -v --tb=short -o faulthandler_timeout=30
  ```

  Collected **196 tests** in the shared workspace at that time. It stalled at
  `tests/api/test_api.py::test_search_filters_and_pages`, inside Starlette `TestClient.__enter__`
  / AnyIO `start_task_soon`, with one thread in an asyncio selector and the main thread waiting
  for a future. The diagnostic printed the stack after 30 seconds and exited **124** at the
  90-second bound. This is not a passing API suite or evidence about the production host.
- `UV_CACHE_DIR=/tmp/govtech-uv-cache uv run --frozen pytest tests/ --ignore=tests/api -q`:
  **146 passed, 2 failed, 1 warning in 27.39s**, exit 1. Both failures occurred creating local
  sockets (`PermissionError: [Errno 1] Operation not permitted`) in the existing decision
  HTTP tests `test_http_client_retries_then_succeeds` and
  `test_http_client_raises_on_payment_required`. One existing AsyncLimiter loop-reuse warning.
  These sandbox results require an unsandboxed parent rerun; no production inference follows.
- No CI, live hosted verification or browser-layout success is claimed.

Concurrent changes appeared during this phase in `src/govtech_scraper/decision/client.py`
and `tests/decision/test_quota_guard.py`; they were not authored or modified by this worker.
They explain why shared-workspace collection exceeds the original 170 plus this worker's
new tests. Preserve and review them with their owner. The final added workflow test brings
this worker's new operations tests to 16; the full-suite collection above preceded that test.

## Unfinished obligations and activation gates

4. **Separate classification workflow/checkpoint runner not implemented.** Do not enable a
   schedule. Parent retains sample evaluation and budget decisions. Implement a manual-only
   bounded runner against an immutable published baseline, durable WAL-safe checkpoint
   artifacts with `if: always()`, model/taxonomy/source provenance, validated resume rejecting
   mismatched baselines, and tests for failure/quota interruption. No checkpoint merge into
   newer metadata/history without a separately tested design. Changed repository inputs must
   not retain stale classification input hashes. Sample/evaluation runs must never publish.
   Require explicit publish opt-in and reviewed coverage/quality acceptance, not `scored>0`,
   and the same publication concurrency group. No unsafe stub workflow was added.
5. **Expanded independent readiness monitor not implemented.** Existing `check_freshness.py`
   still checks dataset freshness and remote artifact integrity. The new rollout check
   verifies public REST stats, including classification counts, but is mutating and must not
   be repurposed as an independent monitor. Add a read-only script with real classification
   coverage/input-hash checks and REST/MCP parity where practical, safe errors and nonzero
   exits for stale/failing state. No external cron has been configured.

Before real rollout, the parent should review the diff, run the full pytest suite with local
socket support, verify production Hub permissions and persisted variables, and perform the
actual authorized publish/restart/public-endpoint check. Operator commands and ambiguous
restart recovery are documented in `docs/operations.md`. Preserve the supplied public
baseline context (`4967e5bb8e143f6eb0c26bedcfef55a291976d33`, current 75,675 / total 78,860 /
994,441 historical snapshots); no live readback of those values was attempted here.
