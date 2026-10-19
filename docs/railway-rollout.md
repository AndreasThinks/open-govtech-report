# Railway publication rollout

The default remains HF-only. Both `scripts/update_index.py` and the worker's explicit
`--publish --rollout` call `railway_rollout.update_public_index`. Neither adds scraping,
source acquisition, inference, or changes to worker budgets. The metadata workflow
continues to publish the same verified HF bundle before requesting rollout.

The worker resumes a pending rollout only while its receipt is still the public dataset head.
A newer publication (normally the weekly scrape) supersedes it: the worker drops it and
publishes on top of the newer head instead of rolling the site back. Failures report a
reason (`failure_reason`): our own fixed error sentences are kept, anything else is
reduced to its exception type.

## Explicit runtime configuration

Set `GOVTECH_RAILWAY_ROLLOUT_ENABLED=true` only after parent acceptance. Unset, empty,
or `false` disables Railway without needing Railway credentials. Other flag values
fail closed. Enabled mode requires all of:

| Variable | Meaning |
| --- | --- |
| `GOVTECH_RAILWAY_PROJECT_ID` | Exact reviewed project ID |
| `GOVTECH_RAILWAY_ENVIRONMENT_ID` | Exact environment ID |
| `GOVTECH_RAILWAY_WEB_SERVICE_ID` | Public web service ID, never the worker |
| `GOVTECH_RAILWAY_WEB_URL` | Reviewed public HTTPS origin, without trailing slash |
| `GOVTECH_RAILWAY_API_TOKEN` | Explicit project token scoped to the reviewed environment |

The workflow only references repository variables and the API token secret; it does
not populate them. The token belongs to the publication caller, never the web
service or its variables/receipts. No CLI, credential files, or automatic token
lookup is used. Before every target read or mutation, the adapter queries
`projectToken { projectId environmentId }` and requires both IDs to match the explicit
configuration. Missing, malformed or mismatched scope fails closed before the target
request; the initial check also precedes any fallback-service restart.

Origins are limited to `https://www.govtechindex.cloud`, the native Index HF host,
and one-label `https://<name>.up.railway.app` hosts. Use the reviewed Railway service
origin for `WEB_URL`; deployment metadata and readiness bind it to the expected code
and dataset. Userinfo, ports, paths, query strings, fragments, encoded aliases and
redirects are rejected. Public HTTP requests have no authentication headers, use a
separate transport, and ignore proxy configuration. Only the fixed
`https://backboard.railway.com/graphql/v2` GraphQL endpoint receives the token, using
`Project-Access-Token: <token>` with `Content-Type: application/json` and the honest
`User-Agent: govtech-index/1.0` (the default urllib user agent was rejected by
Railway). There is no
Bearer fallback. This follows Railway's documented
[project-token authentication](https://docs.railway.com/guides/public-api#using-a-project-token);
account, workspace and OAuth tokens use a different authentication scheme and are
not supported by this adapter.

## Sequence and recovery

The caller must hold the existing shared publication lock across the combined
operation. Coordinate GitHub publication and worker publication with the existing
operations procedure; this module is not a distributed lock service.

1. Validate explicit configuration, verify token project/environment scope, and inspect
   the public service. Require repository
   `AndreasThinks/open-govtech-report`, no image override, web Dockerfile
   `spaces/railway/Dockerfile.web`, no worker command or cron schedule, no volume
   attachments to this service in this environment, no private-worker or credential variables. Source SHA comes from actual
   deployment `meta.commitHash`. Missing or unsupported target metadata fails closed.
2. The retired native HF Space is not updated or checked in Railway mode. It used to
   be a hard gate here; when its pinned code fell behind, it blocked every Railway
   rollout (Oct 2026). The HF-only default path (Railway disabled) still uses it.
3. Reverify the immutable receipt with `verify_remote` and `check_freshness`. The
   database URL always pins the fixed public dataset to its exact 40-hex commit.
4. Persist and read back `GOVTECH_RAILWAY_ROLLOUT_STATE` with phase `prepared`, using
   `skipDeploys:true`. The public JSON includes exact target, origin, dataset and DB
   hashes, source SHA, previous deployment ID, and a hash of the other variables.
5. Batch only `GOVTECH_DB_URL` and the intent's `deploying` phase in one
   `variableCollectionUpsert`, `replace:false`, `skipDeploys:false`. This is the only
   redeployment trigger. There is no deploy/restart API call. Other variables remain
   untouched; no configured code SHA is changed to impersonate a new image.
6. Read back variables and poll deployment plus public `/healthz` and `/v1/stats`.
   Require a different successful deployment, exact dataset SHA and database SHA256,
   readiness source SHA matching deployment `meta.commitHash`, and receipt-matching
   repo count, scrape timestamp and classified count. Reinspect after public reads
   to catch concurrent deployment/config changes. The manifest's source SHA may
   legitimately differ from the web image's source SHA.
7. Finalize `verified` intent with deployment ID and `skipDeploys:true`; read it back.
   Verify canonical `https://www.govtechindex.cloud/v1/stats` before combined success.
   Native success never certifies canonical. The returned report identifies both
   endpoints, the exact Railway target, deployment, source, revision and public checks.

An ambiguous mutation is followed by exact readback, never a blind retry. If the
server accepted it and readback matches, verification proceeds. Otherwise stop for
operator reconciliation. A `prepared` intent left by a crash is intentionally
unresolved: do not resend automatically, because acceptance is not known. A known
same-revision `deploying` intent resumes polling without another mutation that
triggers deployment. A verified revision rechecks without writes. Unknown phases,
different unresolved revisions, target/source/config/deployment drift and mismatched
receipts fail closed. A different revision may replace only a verified intent.

An operator must inspect Railway variables, deployment history and public readiness
before reconciling an unresolved intent. Do not delete an intent simply to retry.
A failure after Railway verification but before canonical verification retains the
verified state; the next call rechecks it without redeploying. The worker retains its
pending receipt on failure, and its existing pending-rollout path retries before
republishing or acquiring provider work.

Polling is 1–60 attempts per phase (default 30), with 10-second sleeps between
attempts and 20-second HTTP timeouts. Errors from transports are sanitized. Partial
success is not written as a combined success report by the script.

## Staging and offline testing

`rollout_railway(receipt, hf_api, RailwayClient(config), ...)` explicitly verifies
only the configured service endpoint. It returns `verified:true` with that endpoint;
it makes no canonical-cutover claim. Production script/worker use the combined
function, which has no native-only success option. `--diagnose-native-fallback`
retains its old HF-only diagnostic meaning and cannot turn failure into success.

`RailwayClient(config, transport=...)` accepts a urllib-style request opener;
`read_public(url, transport=...)` accepts a separate opener. Both production adapters
are exercised with fake transports, alongside real immutable artifact verification.
No Railway or HF API calls are made by these tests.

```sh
mkdir -p .scratch
export TMPDIR="$PWD/.scratch" UV_CACHE_DIR="$PWD/.scratch/uv"
uv run --offline --no-sync pytest tests/railway/test_rollout.py -q \
  --basetemp="$PWD/.scratch/rollout-tests"
```

Cloud acceptance, permissions, real GraphQL schema/service configuration, deployed
image, startup storage capacity, canonical routing and external freshness monitoring
remain parent responsibilities. See `evidence/RAILWAY_ROLLOUT_HANDOFF.md` for exact
local checks and restrictions; local fake tests are not cloud acceptance.

## Volume contract and parent acceptance

ServiceInstance has no `volumeMounts` field. Inspection reads
`project(id:$projectId).volumes.edges.node` with volume `id` and nested
`volumeInstances.edges.node { serviceId environmentId mountPath }` instead.
Every node and identity is validated, including unrelated worker attachments.
A well-formed worker volume is allowed; any attachment matching both the configured
web service and environment is refused. Null/pending identities or mount paths,
missing data, malformed nodes, and refused queries fail closed before rollout.
Complete empty connections are valid, including an unattached volume.

Both connections request `pageInfo { hasNextPage }` and require the literal boolean
`false`. A further page, missing pagination metadata, or invalid metadata stops
rollout; there is no partial-page inspection success or fallback query.

The parent confirmed the user agent correction and volume query on the live API,
including `pageInfo { hasNextPage }` returning `false` on both connections.
The parent's real adapter inspection then exposed Railway's injected
`RAILWAY_PRIVATE_DOMAIN=govtech-web.railway.internal` being rejected by the broad
private-variable guard. Only the exact key `RAILWAY_PRIVATE_DOMAIN` is now exempt,
and only when its value is a string matching
`[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.railway\.internal` with total length at most 253.
The field may be absent; an invalid present value fails closed. All other
secret/private/credential-key guards remain in force, including lookalike keys.
**Full live adapter inspection acceptance remains pending with the parent.**
Offline tests do not establish live acceptance. Hermes owns lifecycle and external
actions.
See `evidence/RAILWAY_API_CONTRACT_HANDOFF.md` for this correction's verification.
