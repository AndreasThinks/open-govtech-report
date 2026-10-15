# Incremental classification and recurring authorization core

These are local library interfaces for the future worker. Existing CLI commands
retain their one-off behavior. No scheduler, network acquisition, remote publishing,
private-state bootstrap, Railway packaging or deployment is introduced here.

## Planning and reuse

`decision.incremental.IncrementalPlanner(db, model=..., prompt_version=...,
taxonomy=...)` returns a typed `IncrementalPlan` from `plan(repo, source=None)`:

| Action | Caller responsibility |
| --- | --- |
| `ACQUIRE` | Obtain current public README evidence, bounded by the existing source builder, and plan again. |
| `CLASSIFY` | New meaningful evidence or a changed model/prompt/taxonomy contract permits classification, subject to authorization and budget. |
| `REUSE` | Call `rebind(repo, plan.source)` to persist the demonstrated freshness relationship. |
| `QUARANTINE` | Keep pending/unknown requests or unverifiable historical evidence out of the paid queue. Reconcile or review explicitly. |

`meaningful_hash(source)` hashes bounded evidence fields, excluding observational
counters and routine dates. A changed `pushed_at` requires acquisition. A different
README blob SHA, full-file length or timestamp does not justify another inference
when the bounded meaningful evidence is identical. Repository identity, description,
country, topics, language, license, branch, fork/archive flags and README text remain
meaningful. Model, prompt version and taxonomy digest/version must also match.

`classify_incremental(db, classifier, repos, sources=..., concurrency=...)` executes
a bounded batch through the existing classifier. It does not fetch sources. It
rebinds reuse, counts quarantines as pending and reports missing acquisitions in
`errors` with `skipped_empty`. The worker should normally resolve `ACQUIRE` before
calling it. Duplicate repository/source URLs are rejected.

The original classification's hashes, model, prompt, taxonomy, status, date, cost,
usage, tags and abstentions are not changed by reuse. A failed classification of
unchanged evidence is retried once its last attempt is six days old (so once per weekly
run, within the period budget); abstentions remain assessed. `evidence_reuse` records original input hash, current
metadata/source hashes, meaningful hash and verification time. The read-only API
accepts exact metadata or an applicable proof, including coverage and filters.
Proofs do not apply to a different inference input or later changed metadata.

`evidence_source_history` retains sources before cache replacement, allowing legacy
classification rows to be compared without purchasing replacement labels. Exact
historical hashes can also prove reuse. Missing historical provenance is quarantined
when equivalence/change cannot be established. Invalid acquisitions never prove
reuse. A stored tag quote that disappeared during otherwise equivalent evidence
also requires review. Unresolved claims with known URLs quarantine those URLs;
claims without any source binding conservatively quarantine the entire increment.

## Metadata merge and review preservation

`merge_public_metadata(db, verified_public_path)` requires an **existing private
master** and a public snapshot already verified by the caller against an immutable
revision and checksum. The function validates SQLite integrity/schema, but does not
verify a remote revision or bootstrap a master.

It merges original repository metadata and accounts only when source timestamps
are newer, unions snapshots by values rather than local autoincrement IDs, and
advances scrape/account outcomes when their timestamps are newer. Repeating the
same merge does not duplicate history. It never imports public billing, inference,
HTTP cache or worker state. Publication conflict handling must fetch/verify the
new baseline and call this merge again before revalidation; remote parent-SHA
checking remains the future worker's responsibility.

Reviewed tag exclusions from public classification abstentions or the durable
`evidence_review_exclusions` table are retained. Publication applies them to its
snapshot, preserving private inference rows. Explicit publication exclusions are
also persisted in the published copy for the next metadata rebase. No review is
silently removed by reuse. Public exports still strip all `openrouter_*` tables
and worker status, including the new authorization and assignment tables.

## Recurring authorization

`RecurringAuthorization` is a frozen dataclass containing:

- `authorization_id`
- `monthly_usd` (at most $5)
- `run_usd` (at most $2)
- timezone-aware `approved_at` and `effective_at`

`approve_authorization(db, authorization)` is an explicit administrative operation.
It persists one immutable policy; repeated identical approval is idempotent.
Changing its ID, caps or timestamps is rejected, including increases and attempted
monthly resets. There is no renewal/rollover/reset operation.

Construct `PeriodBudget(db, authorization=authorization,
increment_id=verified_metadata_revision, enabled=True)` and inject it into the
existing `EvidenceClassifier`. Omitted `enabled`, absent approval, nonmatching
configuration or claims before the effective timestamp fail closed. The future
worker must load explicit operator-approved configuration; no environment-driven
approval is added here. Use the immutable metadata publication identity as
`increment_id`, never a process/job ID. Its deterministic run ID survives restart
and month rollover, and its lifetime $2 cap does not replenish on restart.

Reservation, duplicate checking, policy checking and claim/month assignment occur
in one `BEGIN IMMEDIATE` transaction before POST. Monthly accounting is the sum of
completed recurring claims assigned to that UTC month plus **all** unresolved
holds, including previous-month and historical one-off holds, each counted once.
Each run also accounts for its completed charges and unresolved reservations.
Unused monthly capacity does not accumulate. A changed run ID cannot reset monthly
spend. Concurrent connections cannot both claim the same remaining capacity.

Historical ledger rows and the old `ceiling_usd` setting remain unchanged. An
additive `openrouter_claim_authorizations` table assigns historical claims to
`legacy-one-off` and new recurring claims to the immutable policy and UTC month.
Completed historical one-off costs do not consume recurring capacity; new
recurring costs do not consume the original one-off authority. Unknown legacy
holds remain intact and conservatively constrain recurring spend.

The existing reviewed-price calculation, receipt capture/recovery, exact-generation
reconciliation, uncertain holds and accounting-breach stop remain in use. No guessed
refund, replacement POST or live price lookup is added. The future worker must
validate live reviewed prices before enabling paid work, and retain the private
master and its entire ledger across every restart/restore.

## Local verification

Use the checkout-local environment:

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --frozen pytest
```

See `evidence/RAILWAY_CORE_HANDOFF.md` for actual test results and this execution
sandbox's socket restrictions. The optional evidence-only pipe harness is not
application code and is unnecessary in ordinary environments.
