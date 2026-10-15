# Railway core handoff — Tasks 1 and 2 only

Date: 2026-10-14. Starting commit: `7e1b4cfaf585d34a09bea2afd7d29f47ad61900d`.
Implementation scope: meaningful-input incremental planning/reuse, metadata/history
merge, preserved public review exclusions, read-only freshness, and durable recurring
$5/UTC-month plus $2/increment authorization. See [interface documentation](../docs/railway-core.md).

No credentials, live APIs, production/private databases or external writes were
used. Test databases are synthetic temporary SQLite files. No push, merge or
deployment was performed. No cloud orchestration or Railway Docker packaging was
implemented. The existing CLI has not been switched to recurring paid operation.
No real authorization was installed into a master ledger.

## TDD evidence

Tests were written and run before implementation. Observed red results included:

- Initial focused run: `2 errors in 1.03s`, missing `decision.incremental` and
  `decision.period_budget` modules.
- API/executor/publication regression run: `3 failed, 5 passed in 4.16s`.
  Rebound classifications remained stale, the incremental executor was absent,
  and publication resurrected an excluded tag.
- Accounting/provenance regression run: `3 failed, 15 passed in 4.61s`.
  Recurring spend incorrectly consumed legacy authority capacity, a disappeared
  quote was reused, and missing legacy provenance was eligible for repurchase.
- Changed-evidence regression: `1 failed in 0.87s`, detecting overly broad quote
  quarantine that prevented legitimate changed-input classification.
- Scrape-history regression: `1 failed in 0.52s`, detecting an incomplete historical
  scrape that did not advance after receiving the completed public record.
- Corrupt-cache regression: `1 failed in 0.69s`, detecting acquisition failure while
  archiving malformed legacy cached JSON.

Each was fixed and rerun. Final focused output is in
[railway-core-focused-tests.txt](railway-core-focused-tests.txt):

```text
25 passed in 5.08s
```

The focused command used no socket workaround:

```bash
UV_CACHE_DIR=/tmp/govtech-uv-cache UV_PROJECT_ENVIRONMENT="$PWD/.venv" \
  uv run --frozen pytest tests/decision/test_incremental.py tests/decision/test_period_budget.py -q
```

Coverage includes failed/abstained reuse, bounded evidence and blob/timestamp
changes, legacy migration, missing/corrupt provenance, literal quote checks,
unbound pending claims, API/publication freshness, review exclusions, metadata
conflicts and history idempotence, fake-transport paid classification then reuse,
immutable approval and effective timestamps, concurrent claims, actual on-disk
restart, UTC rollover, old-month holds, separate legacy capacity, exact-generation
reconciliation and accounting-breach stops.

## Full-suite execution and environment limitation

The first literal `uv run --frozen pytest` invocation collected 269 tests at that
stage and stalled in `Starlette TestClient.__enter__`. A diagnostic run with
`faulthandler_timeout=10` showed asyncio's thread wakeup stuck in its selector.
A minimal `socket.socketpair()` probe raised `PermissionError: [Errno 1] Operation
not permitted`. The stalled invocation was interrupted (exit 130).

To run the existing in-process API tests, a temporary, opt-in asyncio self-wakeup
pipe replaced its socket pair. Its exact source is retained at
[railway_test_harness/sitecustomize.py](railway_test_harness/sitecustomize.py).
It changes only asyncio's thread wakeup mechanism; no application transports,
assertions, skips, provider responses or HTTP server behavior are replaced.

Final full-suite command (checkout-local venv, frozen dependencies):

```bash
PYTHONPATH="$PWD/evidence/railway_test_harness" \
UV_CACHE_DIR=/tmp/govtech-uv-cache UV_PROJECT_ENVIRONMENT="$PWD/.venv" \
  timeout 180s uv run --frozen pytest
```

Final output is retained in [railway-core-full-tests.txt](railway-core-full-tests.txt).
```text
2 failed, 279 passed, 1 warning in 40.43s
```

Exit status: 1. The warning is the existing `AsyncLimiter` cross-event-loop reuse warning.

The two failing tests are existing localhost HTTP server tests:

- `tests/decision/test_decision.py::test_http_client_retries_then_succeeds`
- `tests/decision/test_decision.py::test_http_client_raises_on_payment_required`

Both fail during socket creation in `aiohttp.test_utils.TestServer`, before any
application request: `PermissionError: [Errno 1] Operation not permitted`.
They also fail against an exported **unchanged HEAD baseline**, using the same
checkout-local venv and pipe harness: `2 failed in 0.55s`.
See [baseline output](railway-core-baseline-socket-tests.txt).
No tests were skipped or rewritten to hide this restriction. This is **not a claim
of an entirely green full suite**. Rerun the ordinary full command in an environment
permitting local socket operations before merge. The optional pipe harness should
not be used in production.

## Durable interfaces and remaining requirements

1. Restore an existing verified private master, including all historical billing
   rows, from an authenticated private backup pinned by revision and checksum.
   Never bootstrap a new paid ledger from public data. None of this remote restore
   machinery has been implemented or exercised here.
2. Fetch and verify the immutable public metadata revision. Call
   `merge_public_metadata(existing_master, verified_path)` to preserve private
   operational/inference state and newer metadata/history. The core validates
   SQLite structure/integrity, not remote revision authenticity.
3. Use `IncrementalPlanner.plan` to acquire only required public sources. A push
   triggers acquisition; unchanged bounded meaningful evidence permits `rebind`.
   `classify_incremental` executes bounded local batches with supplied evidence.
   It leaves acquisition and quarantine resolution to the worker. Legacy rows
   lacking provable original evidence remain quarantined, never bulk repurchased.
4. Install the explicitly approved immutable `RecurringAuthorization` through the
   administrative interface once; worker configuration must match exactly.
   Inject `PeriodBudget(..., increment_id=verified_revision, enabled=True)` into
   `EvidenceClassifier`. Default is disabled. Validate live reviewed prices first.
   Do not invent a new increment identity on process restart.
5. Use the existing exact-generation reconciler for known receipts. Preserve all
   unknown holds and bindings; never guess refunds or retry uncertain POSTs.
6. Implement Task 3 separately: private bootstrap/backup verification, durable
   volume enforcement, worker lock/deadline/lifecycle/status, immutable revision
   polling, dry-run defaults, bounded public acquisition and paid execution,
   WAL-safe backup, public/private target separation, remote parent-SHA guard,
   conflict rebase/revalidation, literal/caveat review and partial-coverage gates.
7. Implement Task 4 separately: Railway web/worker images and entrypoints, dynamic
   port/readiness/pinned sanitized snapshot, `/data` validation, cron exit/restart
   behavior, staging rollout and canonical verification adapter.
8. Parent verification remains: unrestricted green full suite, UI/browser/package
   checks, independent review, real private-ledger equality/backup restoration,
   controlled restart and mounted-volume checks, bounded authorized paid smoke,
   all REST/MCP parity, staging acceptance and only then deployment/cutover.

The core preserves inference provenance in place. Additive reuse/source-history
and review tables carry evidence freshness and reviewed exclusions. New authority
and assignment tables start with `openrouter_` and are stripped from public bundles
by the existing sanitization policy. Historical one-off ledger rows and the $50
setting remain unchanged in regression tests. Real private-ledger equality has
not been tested because accessing that database was explicitly out of scope.

## Local commit location

The requested worktree commit was attempted, but Git could not create
`/home/avery/.hermes/cache/scratch/govtech-pr3-fix/.git/worktrees/govtech-railway/index.lock`:
`Read-only file system`. This checkout is a linked worktree whose Git metadata is
outside the writable sandbox. No permission escalation or metadata workaround was
used, and the original worktree branch could not be advanced.

The verified files are therefore committed in a separate **local-only clone** at
`/tmp/govtech-railway-core-verified`, on branch `feat/railway-core-verified`.
A durable Git bundle is retained beside this handoff as `railway-core.bundle`.
It contains the commit and can be fetched locally in an authorized checkout; no
remote operation is required. The bundle is an export produced after the commit,
so it is not recursively included in itself. The original checkout retains all
implementation files and verification logs as working-tree changes.
