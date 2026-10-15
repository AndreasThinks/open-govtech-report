# Railway API contract handoff — t_a6205ef5

Recovered in isolated checkout `fix/railway-live-api-contract`. The intentional
three-file prior correction was preserved: honest `govtech-index/1.0` user agent,
project volume/volume-instance inspection, and strict complete-pagination checks.

Parent evidence supplied for this task: 251 tests and independent spec/quality
review passed before this correction. The parent LIVE confirmed the query and
`pageInfo.hasNextPage == false` on BOTH volume connections. Real parent
`adapter.inspect` subsequently rejected the injected benign variable
`RAILWAY_PRIVATE_DOMAIN=govtech-web.railway.internal` through the broad PRIVATE
key guard. These live findings were supplied by the parent, not reproduced here.

The correction exempts ONLY the exact, case-sensitive `RAILWAY_PRIVATE_DOMAIN`
key after validating a string with full match against
`[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.railway\.internal` and total length <=253.
Absence remains permitted; invalid present values fail closed. No additional
label-length rule was introduced beyond the parent's specified contract.
All other secret/private/credential-key guards remain unchanged. User agent,
volume completeness/attachment checks, and project-token safeguards are preserved.

TDD verification used synthetic transport fixtures and scratch temporary files:

- Red: 5 failed, 293 passed in 26.54s. All five failures were valid domain cases
  rejected by the original guard.
- Green: 298 passed in 27.15s. Coverage includes benign domains, the 253/254
  total-length boundary, invalid types/values, uppercase and malformed domains,
  lookalike PRIVATE keys, and the existing forbidden-key families alongside a
  valid exempt field. Existing tests exercise absence of the field.
- `git diff --check` passed.

Commands (run from checkout, offline):

```bash
UV_OFFLINE=1 UV_CACHE_DIR="$PWD/.scratch/uv-cache" TMPDIR="$PWD/.scratch" uv run --frozen pytest tests/railway/test_rollout.py -q -p no:cacheprovider --basetemp="$PWD/.scratch/pytest-private-domain-red"
UV_OFFLINE=1 UV_CACHE_DIR="$PWD/.scratch/uv-cache" TMPDIR="$PWD/.scratch" uv run --frozen pytest tests/railway/test_rollout.py -q -p no:cacheprovider --basetemp="$PWD/.scratch/pytest-private-domain-green"
```

Only the four authorized source/test/documentation/handoff files were edited;
test-generated temporary artifacts remain under the existing `.scratch/`.
No credentials, private database, unrelated home files, network, services, or
board were accessed. No commit or push was made.

Full live inspection acceptance remains pending. Hermes owns lifecycle and all
external actions, including rerunning the real adapter inspection and deciding
whether rollout may proceed. Local fake tests are not live acceptance.
