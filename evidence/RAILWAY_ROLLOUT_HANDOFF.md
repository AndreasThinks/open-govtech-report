# Railway rollout integration handoff

Implementation complete locally; cloud acceptance is not claimed. Default rollout
remains disabled and HF-only. No credentials, private databases, live APIs,
inference, external writes, pushes, deployments or DNS changes were used. Synthetic
SQLite fixtures and fake HF/GraphQL/public transports stayed in checkout scratch.

## Delivery

Baseline: `e7cdbef5035a18c98046efa72e67fb68ed3378e6`.
Implementation commit: `b488637aadc028e29be817850bf84f58cd0b12f7`.
The checkout Git metadata is read-only under the supplied permission profile, so
commits are in an independent local clone:

- Clone: `.scratch/rollout-commit`
- Branch: `railway-rollout-integration`
- Bundle: `.scratch/railway-rollout.bundle`
- Verification clone: `.scratch/rollout-verify`

The implementation commit is followed by a handoff-only commit. Obtain the exact
final delivery commit from `git bundle list-heads .scratch/railway-rollout.bundle`
(`refs/heads/railway-rollout-integration`). The bundle contains both commits since
the baseline. Bundle verification, re-import into a separate clone, exact delivery
file equality, and clean clone status are checked as part of delivery. Scratch
logs, environments, clones and bundles are not committed.

## Interfaces and behavior

`railway_rollout.update_public_index(receipt, api, *, environ=None, railway=None,
fetch=read_public, attempts=30, sleep=time.sleep, allow_native_fallback=False)` is
shared by the metadata script and worker. Disabled mode calls the existing HF
operation. Enabled mode validates the target, updates/verifies native HF, rolls
Railway, then verifies canonical stats. It never certifies canonical from native
success. `operations.update_index` adds only an allowlisted `endpoint` argument;
existing callers retain canonical verification and existing HF intent behavior.

`RailwayClient(RolloutConfig.from_env(environ), transport=...)` is the actual HTTPS
GraphQL adapter with injectable urllib-style transport. It uses bearer auth only
at `https://backboard.railway.com/graphql/v2`; public fetches use a separate,
unauthenticated, nonredirecting transport. It reads variables and service metadata,
checks public repository/Dockerfile/command/no mounts/no credential variables,
and validates actual deployment source provenance. It uses one batched
`variableCollectionUpsert(replace:false, skipDeploys:false)` for the new pin and
intent; preparation and finalization use `skipDeploys:true`. There is no explicit
deploy/restart call. Ambiguous writes require readback and are never blindly resent.

`GOVTECH_RAILWAY_ROLLOUT_STATE` stores public target, dataset/checksum/source,
config fingerprint, phase and deployment identities. Same known deploying intent
resumes polling; verified revision rechecks without redeploying. Unknown/prepared
or different unresolved intent and config/source/deployment drift fail closed.
A verified prior revision permits the next metadata revision.

`rollout_railway(receipt, api, client, ...)` is the explicit staging-only interface;
its endpoint-specific result makes no canonical claim. Combined script/worker
success always requires canonical stats. Public readiness must match the receipt's
dataset SHA/DB hash and actual deployment `meta.commitHash`; data-manifest source
SHA is independent. `railway_web.create_verified_app` prefers Railway's actual
`RAILWAY_GIT_COMMIT_SHA` over its configured local `source_commit` fallback.

Worker wiring now resumes a durable pending rollout before metadata/source work or
inference gates, then returns `phase=rollout-completed`. Provider construction is
lazy after the existing paid-mode gates; normal inference and budget algorithms
are unchanged. Pending receipt survives failure and clears only after combined
success; checkpoint behavior is retained. No private-state, budget, planner,
evidence, classification algorithm or Docker image changes were made.

Environment: only explicit `GOVTECH_RAILWAY_ROLLOUT_ENABLED=true` enables Railway.
Unset/empty/`false` is disabled; other values fail closed. Enabled mode requires
`GOVTECH_RAILWAY_PROJECT_ID`, `GOVTECH_RAILWAY_ENVIRONMENT_ID`,
`GOVTECH_RAILWAY_WEB_SERVICE_ID`, `GOVTECH_RAILWAY_WEB_URL`, and
`GOVTECH_RAILWAY_API_TOKEN`. IDs have no defaults. The token must be provisioned as
project-scoped by the parent. The workflow adds vars/secret references only, without
values. No web-service token is set. Allowed public URLs, bounds, recovery, shared
publication-lock responsibility and all interfaces are documented in
[docs/railway-rollout.md](../docs/railway-rollout.md).

## Actual offline verification

All Python commands used the checkout `.venv` through `uv run --offline --no-sync`;
`TMPDIR=$PWD/.scratch`, `UV_CACHE_DIR=$PWD/.scratch/uv`, and explicit scratch pytest
basetemps were set. No system `/tmp` was used for task artifacts.

- Initial TDD red: new test collection failed because `railway_rollout` did not
  exist (`.scratch/rollout-red.txt`). Further red tests reproduced actual service
  config rejection gaps (`target-red.txt`, 3 failed), worker run entering inference
  gates before pending rollout (`worker-red.txt`, 1 failed), and eager CLI provider
  construction (`cli-red.txt`, 1 failed), before implementing each fix.
- Focused HF/rollout/web/worker check: **149 passed, 1 deselected**, 13.49s
  (`focused-complete.txt`); the deselected historical route test uses TestClient.
  The subsequent CLI regression and all focused tests also ran in the final full
  harness suite below. Final rollout module contains 45 tests.
- Ordinary full suite attempted with `timeout 180 ... pytest -q`: **exit 124**,
  no completed result (`full-tests.txt`). A separate 15s verbose single-test attempt
  stalled at `tests/api/test_api.py::test_search_filters_and_pages`
  (`testclient-proof.txt`), consistent with the known sandbox TestClient limitation.
- Final full suite with the **existing evidence-only pipe wakeup harness**:
  **459 passed, 2 failed, 1 warning in 51.52s** (`all-complete.txt`). Command:
  `PYTHONPATH=$PWD/evidence/railway_test_harness timeout 150 uv run --offline --no-sync pytest -q --basetemp=$PWD/.scratch/all-complete`.
  Both failures are the pre-existing aiohttp socket tests
  `test_http_client_retries_then_succeeds` and
  `test_http_client_raises_on_payment_required`: socket creation raises
  `PermissionError: [Errno 1] Operation not permitted`. The existing AsyncLimiter
  loop-reuse warning remains. **This is not an unrestricted green full suite.**
- `node tests/site_behavior.cjs`: **8 passed, 0 failed** (`node-direct.txt`).
- Browser attempt: `uv run --offline --no-sync python tests/site_browser.py`
  exited 1 at import: **Playwright is not installed** in this checkout environment
  (`browser-tests.txt`). No browser tests are claimed and no installation/network
  access was attempted.
- `uv build --offline --no-build-isolation --out-dir .scratch/dist-complete`:
  **sdist and wheel built successfully** (`build-complete.txt`). Inspected archives
  for rollout-module inclusion; wheel source bytes match final checkout modules,
  and wheel contains no scratch/database content.
- Script `--help` and `git diff --check`: passed.

Tests exercise the actual GraphQL adapter and real HF receipt verifier with fake
transports: disabled/no-credential mode, invalid enabled config, unsafe targets and
URLs, token separation, preserved unrelated variables, single redeploy plus
nondeploying finalization, exact readback, ambiguous accepted/unaccepted writes,
resume/drift/unresolved intents, bounded polling, source-vs-data SHA distinction,
canonical failure despite native success, staging semantics, subsequent metadata
publication after cutover, and pending worker recovery without provider calls.

## Parent acceptance steps

1. Import/review the bundle against the baseline; rerun unrestricted full pytest,
   Node and the 16 browser checks with existing required browser dependencies.
2. Review explicit target IDs and project-scoped token permissions out of band.
   Verify the live GraphQL service metadata shape (including volume mount inspection),
   web Dockerfile/command, public repository and no credentials/private volume on
   the web service. API/schema errors fail closed; fake transports are not schema
   or permission acceptance. Review actual image commit separately from data source.
3. Build/bootstrap the reviewed web image through the parent-owned workstream;
   verify storage capacity and public readiness. First use the explicit staging
   function and immutable verified receipt. Check exactly one redeployment and
   unchanged unrelated variables. Leave the workflow flag disabled until accepted.
4. Coordinate the existing publication lock, enable explicit combined rollout only
   after review, and test native HF fallback plus canonical routing/stats. Check a
   metadata-only publication after traffic cutover and a worker pending-receipt
   resume. Native/staging success alone is not canonical acceptance.
5. Keep independent freshness monitoring. Reconcile unresolved prepared/ambiguous
   intents with exact config/deployment/readiness evidence before any manual retry.
   No cloud action or approval is implied by this local handoff.
