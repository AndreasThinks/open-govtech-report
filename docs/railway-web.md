# Railway public web service

`src/govtech_scraper/railway_web.py` downloads and verifies a public snapshot before
starting the existing FastAPI site, REST API and MCP server. No credentials are
required or loaded. This service must have no private volume or operator secrets.
The worker is a separate workstream; this change supplies no worker implementation.

## Configuration

- `GOVTECH_DB_URL` (required): exactly
  `https://huggingface.co/datasets/AndreasThinks/government-github-repos/resolve/<40 lowercase hex SHA>/data/govtech.db`.
  Branch names, query strings, fragments, userinfo, other datasets and path encodings
  are rejected. The manifest is fetched from `data/manifest.json` at the same SHA.
- `PORT`: Railway's assigned port; defaults to `8080`, bound on `0.0.0.0`.
- `RAILWAY_GIT_COMMIT_SHA`: optional source identity displayed by readiness; missing
  values are reported as null, not inferred from the dataset's source commit.
- `GOVTECH_WEB_DB`: local web-owned offline target, default `data/govtech.db`;
  the image sets `/app/data/govtech.db`. Never point this at the private master.

The brief identifies dataset SHA `136b796d21fee72d8149180222f18c745ca9c865`.
This implementation has not downloaded or independently verified that live revision.
A parent operator must confirm its manifest before deployment.

## Verification and readiness

Downloads use bounded reads and a sibling temporary file. The manifest is limited
to less than 1 MiB. Database bytes and SHA256 must match, SQLite integrity, required
schema and foreign keys must validate, and database counts, scrape timestamp and
coverage must equal the manifest. Operator objects named `openrouter_*` or
`worker_status` are rejected, case-insensitively. HTTP partial responses and
conflicting revision headers are rejected. HTTPS redirects are restricted to HF
and its `hf.co` storage hosts. Environment proxy configuration is ignored; no
Authorization header or HF credential lookup is used.

Only a verified file replaces the target atomically. Failure removes the temporary
file, preserves the previous snapshot and stops startup. It does not silently serve
an unverified cached file. The target must have no active readers/writers or WAL/SHM
sidecars; restart the service to change revisions. Use ephemeral web storage, with
one bootstrap per target. Verification trusts the allowlisted publisher's manifest;
it is not an independent signature or classification-quality certification.

`GET /healthz` precedes the static `/` mount and returns a cheap in-memory result:
`healthy`, `source_commit`, `dataset_sha`, `db_sha256`, `publication_timestamp`,
`scrape_timestamp`, and `coverage`. It performs no download or SQLite query.
Healthy means startup verification succeeded; it does not certify current scrape
freshness or continuously check disk integrity. A snapshot older than 14 days stays
usable, and incomplete scrape coverage remains explicitly visible. Monitor the
reported timestamps and coverage separately. Existing `/`, `/v1/...`, `/api`,
`/docs`, and `/mcp/` behavior is retained.

## Build and run (parent/operator)

From the repository root:

```sh
docker build -f spaces/railway/Dockerfile.web -t govtech-web .
docker run --rm -p 8080:8080 \
  -e GOVTECH_DB_URL=https://huggingface.co/datasets/AndreasThinks/government-github-repos/resolve/136b796d21fee72d8149180222f18c745ca9c865/data/govtech.db \
  -e PORT=8080 -e RAILWAY_GIT_COMMIT_SHA="$SOURCE_COMMIT" govtech-web
```

These commands require network reads and were not executed in the offline sandbox.
Set Railway's Dockerfile path to `spaces/railway/Dockerfile.web` and health check
path to `/healthz`. Allow startup time for downloading and validating the full DB.
The image uses uv 0.11.7, `uv sync --locked --no-dev --extra api --no-editable`, and
runs as UID 1000. Its Dockerfile-specific ignore file allowlists only source,
README and dependency manifests; local `.venv`, databases, secrets and private state
are excluded from the build context. No additional project dependencies were added.

## Offline development checks

All temporary files and the uv cache must stay inside scratch:

```sh
mkdir -p .scratch
export TMPDIR="$PWD/.scratch" UV_CACHE_DIR="$PWD/.scratch/uv"
uv run --offline --no-sync pytest tests/railway -q --basetemp="$PWD/.scratch/pytest-web"
uv run --offline --no-sync pytest -q --basetemp="$PWD/.scratch/pytest-all"
uv build --offline --no-build-isolation
```

The commands assume checkout-local `.venv` dependencies are already installed.
The build fallback uses installed setuptools/wheel because isolated offline build
requirements were not cached. See `evidence/RAILWAY_WEB_HANDOFF.md` for actual
sandbox results and required host validation.
