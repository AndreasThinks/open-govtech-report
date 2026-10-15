# Railway project-token authentication correction

Only `src/govtech_scraper/railway_rollout.py`, `tests/railway/test_rollout.py`,
`docs/railway-rollout.md`, and this handoff are changed.
Base commit: `08efe3f`.

Railway's official [public API guide](https://docs.railway.com/guides/public-api)
(checked 2026-10-14; redirects to `/integrations/api`, “Using a project token”)
documents environment-scoped project tokens with `Project-Access-Token`, and
`query { projectToken { projectId environmentId } }` for scope verification.
The earlier brief's Bearer requirement was incorrect for project tokens.

The adapter now sends only the project-token authentication header and JSON
content type to the fixed `https://backboard.railway.com/graphql/v2` endpoint.
Before each target query or mutation it verifies both returned scope IDs against
explicit configuration. There is no cached scope or Bearer fallback. Missing,
malformed, errored, or mismatched scope blocks the target request. Combined rollout
checks this before HF restart as well. Public transports remain separate and carry
only their existing cache-control header. Transport errors remain sanitized.

Snapshot verification, intent transitions, ambiguous-write readback, idempotence,
default-disabled behavior, polling bounds, and inference budgets are unchanged.
Each target request now incurs one extra scope query, with the existing timeout.
No credentials, live APIs, private database, external writes, push or deployment
were used. Only public documentation was read online; runtime validation uses
fake transports and synthetic fixtures. Local tests do not certify cloud acceptance.

## Offline TDD and validation

Used the existing local `.venv` through `uv run --offline --no-sync`, with
`TMPDIR="$PWD/.scratch"`, `UV_CACHE_DIR="$PWD/.scratch/uv"`, and all pytest
`--basetemp` directories under `.scratch`; no system temporary directory.

- Red, before production edits: `pytest tests/railway/test_rollout.py -q`:
  **46 failed, 26 passed**. Fake transport requires the exact project header;
  the old adapter sends Authorization/Bearer. Scope tests also fail because the
  original adapter issues target requests without the documented scope query.
- Green, same focused suite: **72 passed in 4.75s**. Tests cover matching scope,
  project and environment mismatches, malformed scope/GraphQL responses, direct
  writes, refusal before combined side effects, fixed URL, exact headers,
  token-free public requests/config repr/variables/intents/reports, and existing
  rollout and worker-budget regressions.
- Unmodified full suite stalled and was interrupted. A timed repeat with
  `-o faulthandler_timeout=20` shows the first API fixture blocked inside
  `starlette.testclient.__enter__` through AnyIO. A broader unmodified run also
  stalled and was interrupted. Neither is reported as a passing run.
- Full suite with the existing sandbox-only asyncio pipe harness:
  **488 passed, 2 failed, 1 warning in 71.75s**. Command:
  `PYTHONPATH="$PWD/evidence/railway_test_harness:$PWD/src:$PWD" timeout 120s uv run --offline --no-sync pytest -q -o faulthandler_timeout=30 --basetemp="$PWD/.scratch/full-harness"`
  (same scratch environment, plus `HF_HUB_OFFLINE=1`). The two failures are
  `tests/decision/test_decision.py::test_http_client_retries_then_succeeds` and
  `test_http_client_raises_on_payment_required`: local aiohttp server socket
  creation raises sandbox `PermissionError: [Errno 1] Operation not permitted`.
  The warning is existing AsyncLimiter reuse. No assertions or sandbox permissions
  were changed; the existing harness only substitutes asyncio's wakeup pipe.
- `git diff --check` passed.

Logs: `.scratch/red.log`, `.scratch/green.log`, `.scratch/full.log`,
`.scratch/full-diagnostic.log`, `.scratch/broader.log`, `.scratch/full-harness.log`.

## Local delivery

Shared worktree Git metadata is read-only. The commit is created using the local
clone `.scratch/commit-repo/.git`, staging only the four allowed paths from this
checkout. The original branch and shared metadata are unchanged.
The bundle `.scratch/project-token-auth.bundle` contains the commit and reachable
history. The final response records its commit ID and verification result.
