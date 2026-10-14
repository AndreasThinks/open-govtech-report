"""Fail-closed, public-only Railway bootstrap over an immutable dataset revision."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

from .publication import connect_readonly, database_metadata

DATASET = 'https://huggingface.co/datasets/AndreasThinks/government-github-repos'
CHUNK = 1024 * 1024
MAX_SNAPSHOT_BYTES = 8 * 1024**3
DISK_RESERVE_BYTES = 256 * 1024**2


def _validate_max_bytes(value):
    if type(value) is not int or not 0 < value <= MAX_SNAPSHOT_BYTES:
        raise ValueError('Snapshot maximum must be a positive integer at most 8 GiB')
    return value


@dataclass(frozen=True)
class VerifiedSnapshot:
    path: Path
    dataset_sha: str
    db_sha256: str
    publication_timestamp: str
    scrape_timestamp: str | None
    coverage: dict


def parse_source(url: str) -> str:
    """Accept only the canonical public dataset and a full immutable commit."""
    match = re.fullmatch(re.escape(DATASET) + r'/resolve/([0-9a-f]{40})/data/govtech\.db', url)
    if not match:
        raise ValueError('GOVTECH_DB_URL must pin the allowed public dataset to a 40-hex SHA')
    return match[1]


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    """Allow HTTPS HF storage redirects, never arbitrary hosts or local URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or parsed.port not in (None, 443)
                or not (host == 'huggingface.co' or host.endswith('.huggingface.co')
                        or host.endswith('.hf.co'))):
            raise ValueError('Unsafe dataset redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _response(opener, url, revision):
    response = opener(urllib.request.Request(url, headers={'Accept-Encoding': 'identity'}), timeout=60)
    if (response.status != 200 or response.headers.get('Content-Range')
            or response.headers.get('Content-Encoding', 'identity') != 'identity'
            or response.headers.get('X-Repo-Commit', revision) != revision):
        response.close()
        raise ValueError('Partial or different-revision response')
    return response


def _timestamp(value):
    if not isinstance(value, str) or datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('Invalid timestamp')


def bootstrap(url: str, target: Path, *, opener=None,
              max_bytes: int = MAX_SNAPSHOT_BYTES) -> VerifiedSnapshot:
    """Stream and verify before replacement. Target must be offline and web-owned.

    No fallback to an unverified local file; failure stops startup while retaining
    the previous bytes. Scrape age is reported, never used as an availability gate.
    """
    max_bytes = _validate_max_bytes(max_bytes)
    revision = parse_source(url)
    target = Path(target)
    if target.is_symlink() or any(Path(str(target) + s).exists() for s in ('-wal', '-shm')):
        raise ValueError('Target must be an offline regular snapshot')
    # Explicitly ignore environment proxy configuration and never load HF credentials.
    opener = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}), PublicRedirects()).open
    with _response(opener, url.rsplit('/', 1)[0] + '/manifest.json', revision) as response:
        raw = response.read(CHUNK)
        if len(raw) == CHUNK:
            raise ValueError('Manifest too large')
    try:
        manifest = json.loads(raw)
        artifact = manifest['files']['govtech.db']
        size, digest = artifact['bytes'], artifact['sha256']
        if (type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
                or manifest['export_verified'] is not True
                or not re.fullmatch(r'[0-9a-f]{40,64}', manifest['source_commit'])
                or type(size) is not int or size <= 0
                or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or not isinstance(manifest['counts'], dict)
                or not isinstance(manifest['coverage'], dict)):
            raise ValueError('Invalid manifest metadata')
        _timestamp(manifest['publication_timestamp'])
        if manifest['scrape_timestamp'] is not None:
            _timestamp(manifest['scrape_timestamp'])
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('Malformed publication manifest') from None
    if size > max_bytes:
        raise ValueError('Snapshot exceeds configured maximum')
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < size + DISK_RESERVE_BYTES:
        raise ValueError('Insufficient free space for snapshot plus 256 MiB reserve')
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.partial', delete=False) as out:
            temp = Path(out.name)
            count = 0
            checksum = hashlib.sha256()
            deadline = time.monotonic() + 600
            with _response(opener, url, revision) as response:
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError('Snapshot transfer deadline exceeded')
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > size:
                        raise ValueError('Snapshot exceeds advertised size')
                    checksum.update(chunk)
                    out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if count != size or checksum.hexdigest() != digest:
            raise ValueError('Snapshot checksum or byte count mismatch')
        with closing(connect_readonly(temp)) as conn:
            names = [r[0].lower() for r in conn.execute("SELECT name FROM sqlite_master")]
            if any(n.startswith('openrouter_') or n == 'worker_status' for n in names):
                raise ValueError('Snapshot contains private operator tables')
        # database_metadata calls validate_database (integrity, schema and foreign keys).
        counts, scraped, coverage = database_metadata(temp)
        if (counts, scraped, coverage) != (manifest['counts'], manifest['scrape_timestamp'], manifest['coverage']):
            raise ValueError('Manifest does not describe snapshot')
        os.replace(temp, target)
        return VerifiedSnapshot(target, revision, digest, manifest['publication_timestamp'], scraped, coverage)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def create_verified_app(snapshot: VerifiedSnapshot, *, source_commit: str | None = None):
    from fastapi.routing import APIRoute
    from .api.app import create_app
    from .api.queries import Catalogue

    app = create_app(Catalogue(str(snapshot.path)))
    health = {
        'healthy': True, 'source_commit': os.environ.get('RAILWAY_GIT_COMMIT_SHA') or source_commit,
        'dataset_sha': snapshot.dataset_sha, 'db_sha256': snapshot.db_sha256,
        'publication_timestamp': snapshot.publication_timestamp,
        'scrape_timestamp': snapshot.scrape_timestamp, 'coverage': snapshot.coverage,
    }

    def readiness():
        return health

    # The existing '/' StaticFiles mount must not swallow this route.
    app.router.routes.insert(0, APIRoute('/healthz', readiness, methods=['GET']))
    return app


def main():
    import uvicorn

    port = int(os.environ.get('PORT', '8080'))
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be between 1 and 65535')
    configured_max = os.environ.get('GOVTECH_WEB_MAX_DB_BYTES', str(MAX_SNAPSHOT_BYTES))
    if not re.fullmatch(r'[0-9]+', configured_max):
        raise ValueError('GOVTECH_WEB_MAX_DB_BYTES must be an integer byte count')
    max_bytes = _validate_max_bytes(int(configured_max))
    snapshot = bootstrap(os.environ.get('GOVTECH_DB_URL', ''),
                         Path(os.environ.get('GOVTECH_WEB_DB', 'data/govtech.db')),
                         max_bytes=max_bytes)
    app = create_verified_app(snapshot, source_commit=os.environ.get('RAILWAY_GIT_COMMIT_SHA'))
    uvicorn.run(app, host='0.0.0.0', port=port)


if __name__ == '__main__':
    main()
