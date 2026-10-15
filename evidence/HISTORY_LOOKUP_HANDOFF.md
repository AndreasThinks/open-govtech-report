# History deduplication lookup handoff

Scope: only `decision/incremental.py`, `tests/decision/test_incremental.py`,
and this handoff. Base: `6cb2e201748527b69af0ab1a02cc07fe4627c4e0`.

The parent reported 1,070,135 snapshots, a 70,943-row timestamp bucket,
and five lookups taking 0.100886s versus 0.000573s with the URL index.
Those are supplied observations, not measurements reproduced here. No real
private database, credentials, remote services, push, or deployment were used.

`merge_public_metadata` now creates the nonunique, two-column
`idx_snapshots_url_timestamp` index with `IF NOT EXISTS`, inside the existing
merge transaction. The history subquery uses `INDEXED BY` to prevent SQLite
planner versions or statistics from selecting the timestamp-only index.
The complete non-ID-column `IS` predicate is unchanged. The index narrows
candidates; it does not redefine identity or impose uniqueness. Existing
duplicates survive, repeated incoming values deduplicate, overlapping local IDs
do not discard distinct history, and NULL comparisons remain exact. Metadata
upserts, private-state isolation, and transaction boundaries are unchanged.
First use must build the index over existing history, costing time and disk;
production build duration was not measured.

## Offline validation

Used the existing `.venv` via `uv run --no-sync`, with `UV_OFFLINE=1`,
`UV_CACHE_DIR="$PWD/.scratch/uv-cache"`, and `TMPDIR="$PWD/.scratch"`.
All pytest `--basetemp` paths were in checkout `.scratch`; no system `/tmp`.
Runtime: Python 3.11.15, SQLite 3.50.4. No cross-version execution claimed.

- TDD red: `python -m pytest tests/decision/test_incremental.py -k history_merge -q`:
  1 failed, 1 passed. The actual traced merge statement's EXPLAIN plan selected
  `idx_snapshots_timestamp (scrape_timestamp=?)` with 20,000 synthetic rows
  sharing a timestamp and automatic indexes disabled.
- Green: `python -m pytest tests/decision/test_incremental.py -q`:
  **62 passed in 8.79s**. EXPLAIN now requires a SEARCH constraining
  `(html_url=? AND scrape_timestamp=?)`. The value-union regression varies each
  non-ID column, exercises NULLs, duplicate incoming/existing values, ID
  collisions, and repeat imports. Existing metadata/private-state tests pass.
- Full suite: `python -m pytest -q` stalled on the first API fixture and was
  interrupted. A repeat with `-o faulthandler_timeout=20` and a 45-second shell
  timeout captured a wait in `starlette.testclient.__enter__`, through AnyIO,
  at `tests/api/test_api.py:30`. Full-suite success is **not** claimed.
- Broader suite: `python -m pytest tests/decision tests/reliability tests/tagger -q`:
  **254 passed, 2 failed, 1 warning in 35.24s**. Both failures are in
  `test_decision.py`: `test_http_client_retries_then_succeeds` and
  `test_http_client_raises_on_payment_required`; their local aiohttp TestServer
  socket creation raises sandbox `PermissionError: [Errno 1] Operation not permitted`.
  No sandbox permissions were relaxed. The warning concerns AsyncLimiter reuse.
- `git diff --check` passed.

## Local commit delivery

Shared worktree Git metadata is read-only. The local commit is therefore made
using `.scratch/commit-repo/.git`, a local clone of the base, with this checkout
as its work tree and only the three allowed files staged. The original branch
and shared metadata remain untouched.

Bundle: `.scratch/history-lookup.bundle`, containing the fix commit and full
reachable history. Verify with `git bundle verify .scratch/history-lookup.bundle`;
inspect its commit with `git bundle list-heads .scratch/history-lookup.bundle`.
The final response records the commit ID after creation. Local logs are
`.scratch/full-diagnostic.log` and `.scratch/broader-tests.log`.
