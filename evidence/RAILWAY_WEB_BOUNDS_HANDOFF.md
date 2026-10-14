# Railway web snapshot bounds handoff — 2026-10-14

Addresses the oversized-download blocker in
`/home/avery/.hermes/reports/govtech-railway-20261014/web-quality-review.md`.
Base: `535d8438963c52aeba47fc1f45302021b1a06ec6`.
Only `src/govtech_scraper/railway_web.py`, `tests/railway/test_web.py`,
`docs/railway-web.md`, and this handoff are included in the commit.

## Implementation

- Hard maximum: 8 GiB (`8 * 1024**3`). `bootstrap(max_bytes=...)` permits a
  positive integer at or below that cap, excluding booleans. Runtime
  `GOVTECH_WEB_MAX_DB_BYTES` accepts decimal digits and applies the same range.
  Invalid configuration fails before network access.
- Oversized manifests fail before temporary-file creation or opening the DB
  response. Default hard-cap and explicit lower-limit rejection are covered.
- `shutil.disk_usage(target.parent).free` must be at least advertised bytes plus
  256 MiB. It checks the sibling temporary file's filesystem, before creating
  that file or transferring the DB. Tests patch this probe and check its path.
  Exactly sufficient space succeeds; one byte less fails without a DB read.
- Existing stream size enforcement rejects the first overshooting chunk before
  writing it, closes the response, removes the partial file and retains the old
  target. A two-chunk fake transport fails if read again after overshoot.
- Pinned source/redirect checks, hash and SQLite validation, privacy exclusions,
  metadata matching, no-credential transport and atomic replacement remain intact.
  The space probe is a preflight check, not protection from concurrent disk use.

## TDD and verification

All Python commands used checkout-local `.venv` via
`UV_CACHE_DIR="$PWD/.scratch/uv" uv run --offline --no-sync --python .venv/bin/python`.
No dependency installation or live API access was used.

1. Added the explicit-limit+1 test first, with a fake opener that fails if the DB
   response is opened and a tempfile factory that fails if called. The prior
   target must remain unchanged. Running `pytest tests/railway/test_web.py -q
   -k manifest_above_limit --basetemp="$PWD/.scratch/pytest-bounds-red"` failed:
   **1 failed, 41 deselected**, because `max_bytes` was not yet supported.
2. After implementation the same test passed: **1 passed, 41 deselected**.
3. Final focused non-HTTP run:
   `pytest tests/railway/test_web.py -q -k 'not good_historical'
   --basetemp="$PWD/.scratch/pytest-web-bounds-final"`:
   **66 passed, 1 deselected in 3.15s**. Includes all new boundaries and invalid
   argument/environment cases, existing failure/privacy checks and readiness routing.
4. Complete focused and full-suite runs were attempted. Initial stalled runs
   were interrupted (130). Diagnostic reruns used `-o faulthandler_timeout=10`
   and external timeouts (25 seconds focused, 30 seconds full, INT followed by
   kill after 5 seconds); both exited **137**, not success. Focused execution
   stalled at the historical snapshot HTTP route test after 26 passing dots.
   The full suite collected **348 tests** and stalled at
   `tests/api/test_api.py::test_search_filters_and_pages`. Both tracebacks show
   Starlette TestClient waiting in AnyIO `start_task_soon`/thread portal, consistent
   with the earlier sandbox handoff. Logs: `.scratch/bounds-focused.log` and
   `.scratch/bounds-full.log`. No complete focused/full-suite pass is claimed.
5. `git diff --check` passed. Complete HTTP and full-suite execution outside this
   sandbox remain host follow-ups. Docker/live deployment was not attempted.

## Local delivery

The source worktree contains the completed changes. Its Git metadata is read-only,
so the local commit is on branch `fix/railway-web-bounds` in the independent clone
`.scratch/bounds-commit`, based on the exact base above. A one-commit bundle is
provided at `.scratch/railway-web-bounds.bundle`; `git bundle verify` checks it.
The commit contains only the four scoped files, copied byte-for-byte from this
worktree. The final response supplies its commit ID.

No worker, core, Docker or workflow files were changed. No credentials, private
databases, live APIs, external writes, pushes or deployments were used. Test DBs
are synthetic fixtures. Scratch logs, test artifacts and the clone are not committed.
