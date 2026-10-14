# Railway web handoff — 2026-10-14

Branch: `feat/railway-web`. Baseline: `7e1b4cfaf585d34a09bea2afd7d29f47ad61900d`.
Scope: public web bootstrap, web image, offline regression tests and documentation.
No budget/incremental/decision/worker implementation was changed. No credentials,
live API calls, push, merge, deploy or DNS changes were performed.

## Delivered paths

- `src/govtech_scraper/railway_web.py`: injectable public download bootstrap,
  immutable allowlisted URL, manifest/hash/size/SQLite/metadata checks, private table
  rejection, atomic replacement, readiness and runnable module entrypoint.
- `spaces/railway/Dockerfile.web` and `Dockerfile.web.dockerignore`: locked API
  installation with uv 0.11.7, non-root service and minimal build context.
- `tests/railway/test_web.py`: offline fake transports, streaming read bounds,
  historical snapshots, corruption/truncation/oversize/malformed metadata, wrong
  revision/partial responses, private tables, redirects, interrupted transfers,
  SQLite validation, WAL target rejection, readiness/static ordering and port wiring.
- `docs/railway-web.md`: configuration, operational semantics and parent commands.

## Actual TDD and validation evidence

All commands used checkout-local `.venv` through uv, with `TMPDIR=$PWD/.scratch`
and `UV_CACHE_DIR=$PWD/.scratch/uv`. Raw logs remain in checkout-local `.scratch/`
(untracked; not part of the deliverable).

1. Before implementation: `uv run --offline --no-sync pytest tests/railway -q
   --basetemp=$PWD/.scratch/pytest-red` failed collection with
   `ModuleNotFoundError: No module named 'govtech_scraper.railway_web'` (`red.log`).
2. Initial implementation run caught a fixture issue: private-table changes were
   still in the SQLite WAL. The fixture now checkpoints the WAL before serving its
   fake snapshot. The rejection tests subsequently passed.
3. Final focused command: `uv run --offline --no-sync pytest tests/railway -q
   -k 'not good_historical' --basetemp=$PWD/.scratch/pytest-final`:
   **40 passed, 1 deselected in 2.97s** (`focused.log`). Includes direct Starlette
   route matching and readiness assertions without network/thread portal startup.
4. Full HTTP/static regression attempted with `timeout 25 ... pytest tests/railway
   -q -k good_historical -o faulthandler_timeout=10` (`route.log`). It timed out;
   stack trace shows Starlette TestClient `__enter__` waiting in AnyIO's
   `start_task_soon`/thread portal and selector. This test is retained for host execution.
5. Full suite invoked with `timeout 180 uv run --offline --no-sync pytest -q
   --basetemp=$PWD/.scratch/pytest-full` (`full.log`). It stalled without a completed
   result and was interrupted (exit 130); no full-suite pass is claimed. Parent must rerun outside this sandbox.
6. Additional existing regressions: `uv run --offline --no-sync pytest
   tests/test_db.py tests/test_models.py tests/tagger tests/decision -q
   --basetemp=$PWD/.scratch/pytest-regression`: **121 passed, 2 failed in 3.18s**.
   Both failures (`test_http_client_retries_then_succeeds` and
   `test_http_client_raises_on_payment_required`) raise `PermissionError: [Errno 1]
   Operation not permitted` at socket creation (`regression.log`).
7. `uv build --offline` could not resolve uncached setuptools/wheel.
   `uv build --offline --no-build-isolation` succeeded, producing
   `dist/govtech_scraper-0.1.0.tar.gz` and `dist/govtech_scraper-0.1.0-py3-none-any.whl`.
   Zip inspection confirmed bootstrap, site/index.html and decision/taxonomy.yaml.
8. Docker daemon probe failed: `permission denied while trying to connect to the
   docker API at unix:///var/run/docker.sock`. No image build or runtime success
   is claimed. `git diff --check` passed.

## Parent follow-up

Run all 41 Railway tests and the full existing suite on the host. Build the Docker
image from repository root and smoke-test `/healthz`, `/`, REST and MCP. Verify the
pinned dataset's manifest exists and passes bootstrap. Configure only the public
URL, Railway port and source commit; no HF/OpenRouter/GitHub tokens or private
volumes belong in this service. Test an intentional bad pin before production to
confirm startup/readiness failure. Deployment remains entirely with the parent.

Readiness exposes publication and scrape timestamps plus actual coverage. It does
not reject old historical snapshots, and it does not claim incomplete coverage is
fresh or complete. Failed startup preserves old local bytes but does not serve them
without re-verification. A different dataset requires a reviewed allowlist code
change, not an arbitrary URL override.

## Local commit storage limitation

The original worktree's Git metadata is mounted read-only: `git add` failed creating
`govtech-pr3-fix/.git/worktrees/govtech-railway-web/index.lock`. A local clone with
independent Git metadata was created at `.scratch/local-commit`; the scoped files
are committed there on `feat/railway-web` using this checkout as the working tree.
The original worktree therefore still lists these deliverables as untracked.
The parent can import the commit without any remote write:

```sh
git fetch /home/avery/.hermes/cache/scratch/govtech-railway-web/.scratch/local-commit feat/railway-web
git cherry-pick FETCH_HEAD
```

Use a clean parent checkout for that cherry-pick (the original untracked copies
would otherwise conflict). The final response supplies the exact commit ID.
