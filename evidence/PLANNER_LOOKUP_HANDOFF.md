# Quarantine planner receipt lookup

Base: `535d8438963c52aeba47fc1f45302021b1a06ec6`.

Only the receipt join in `IncrementalPlanner._quarantined` changes:
`r.request_hash=l.request_hash AND r.claim_id=l.id`. This uses the existing
receipt primary-key index while requiring both identities. No schema, worker,
web, budget policy, status predicate, or source-precedence changes.

The parent reported five real-data plans taking 13.565553 seconds versus
0.000644 seconds for five indexed pending lookups. Those measurements were
supplied in the task, not reproduced here. All new validation uses synthetic
temporary/in-memory databases; no private database, credentials, remote service,
or external write was used. No real-data speedup is claimed.

## Red/green evidence

The new regression captures the SQL actually executed by `_quarantined` using
SQLite's trace callback, then runs `EXPLAIN QUERY PLAN` with automatic indexes
disabled. Before the production edit, focused tests reported **7 failed,
53 passed in 9.30s**: the plan regression failed on `SCAN r LEFT-JOIN`, and six
wrong-request-hash cases demonstrated the old join accepting an unrelated receipt.

After the one-line edit, focused tests reported **60 passed in 7.87s**.
An independent synthetic in-memory EXPLAIN returned:

```text
SEARCH l USING INDEX idx_openrouter_budget_status (status=?)
SEARCH r USING INDEX sqlite_autoindex_openrouter_response_receipts_1 (request_hash=?) LEFT-JOIN
SEARCH b USING INDEX sqlite_autoindex_openrouter_pending_bindings_1 (request_hash=?) LEFT-JOIN
```

The 44-case semantic matrix covers reserved, uncertain, accounting-breach, and
completed claims; missing/NULL receipt sources; known bindings; matching
receipts; conflicting receipt/binding sources in both directions; and wrong
claim IDs or request hashes, with and without bindings. Unknown pending claims
still quarantine every repository. Known sources quarantine only their repository.
A matching receipt's source still takes precedence over a conflicting binding.
Receipts must satisfy both identities; otherwise the binding/unknown fallback
applies. Completed claims do not quarantine.

## Commands and full-suite limitations

All Python commands used the checkout `.venv` through `uv`, with no dependency
sync or network resolution:

```bash
UV_OFFLINE=1 UV_CACHE_DIR=/tmp/govtech-planner-uv-cache \
  uv run --no-sync --python .venv/bin/python pytest -q tests/decision/test_incremental.py
```

The ordinary full `pytest -q` stalled without test results and was interrupted
(exit 130). A repeat with `-o faulthandler_timeout=30` captured the stall in
`tests/api/test_api.py:30`, waiting in the Starlette/AnyIO test-client setup;
that diagnostic run was also interrupted (exit 130).

The repository's existing sandbox-only asyncio pipe harness allowed the full
suite to finish:

```bash
PYTHONPATH="$PWD/evidence/railway_test_harness:$PWD/src" \
PYTHON_DOTENV_DISABLED=1 UV_OFFLINE=1 UV_CACHE_DIR=/tmp/govtech-planner-uv-cache \
  timeout 180 uv run --no-sync --python .venv/bin/python pytest -q
```

Result: **2 failed, 365 passed, 1 warning in 60.49s**, exit 1. The failures are
`test_http_client_retries_then_succeeds` and
`test_http_client_raises_on_payment_required` in `tests/decision/test_decision.py`.
Both fail creating local server sockets with `PermissionError: [Errno 1]
Operation not permitted`. Both also failed against the unchanged base checkout
with the same venv/harness (**2 failed in 0.58s**). The warning is existing
AsyncLimiter cross-event-loop reuse. No tests were skipped or modified to hide
these restrictions. An unrestricted green full-suite run is not claimed.

Raw session logs are in `/tmp/planner-red.log`, `/tmp/planner-focused.log`,
`/tmp/planner-full.log`, `/tmp/planner-full-diagnostic.log`,
`/tmp/planner-full-harness.log`, and `/tmp/planner-baseline.log`.

## Local delivery

`git add` in the supplied worktree failed because shared Git metadata is
read-only (`index.lock: Read-only file system`). The three allowed files are
committed in the local-only clone `/tmp/govtech-planner-verified`, branch
`fix/quarantine-planner-receipt-lookup`. The bundle is
`/tmp/govtech-planner-lookup.bundle`; delivery verification checks the bundle,
restores it into another local clone, and compares all three files to this
checkout. Artifacts stay in `/tmp` so no additional repository files change.
The original worktree retains the three changes for review.
