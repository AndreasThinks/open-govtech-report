import hashlib
import io
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from govtech_scraper.publication import database_metadata
from govtech_scraper.railway_web import bootstrap, create_verified_app, parse_source, main
from govtech_scraper.railway_web import MAX_SNAPSHOT_BYTES, DISK_RESERVE_BYTES

SHA = 'a' * 40
URL = f'https://huggingface.co/datasets/AndreasThinks/government-github-repos/resolve/{SHA}/data/govtech.db'


class Stream(io.BytesIO):
    headers = {}
    status = 200

    def read(self, size=-1):
        assert 0 < size <= 1024 * 1024
        return super().read(size)


@pytest.fixture
def bundle(tmp_db, sample_repo, tmp_path):
    tmp_db.save_repositories([sample_repo])
    tmp_db.conn.execute("UPDATE repositories SET last_scraped='2020-01-01T00:00:00+00:00'")
    tmp_db.conn.commit()
    source = tmp_path / 'source.db'
    with sqlite3.connect(source) as dest:
        tmp_db.conn.backup(dest)
    def make():
        data = source.read_bytes()
        counts, scraped, coverage = database_metadata(source)
        manifest = dict(schema_version=1, source_commit='b' * 40,
                        publication_timestamp='2020-02-01T00:00:00+00:00',
                        scrape_timestamp=scraped, coverage=coverage, counts=counts,
                        export_verified=True, files={'govtech.db': {
                            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}})
        return data, manifest
    return source, make


def transport(data, manifest):
    def open_url(request, timeout):
        assert not request.has_header('Authorization')
        assert request.full_url in (URL, URL.replace('govtech.db', 'manifest.json'))
        return Stream(json.dumps(manifest).encode() if request.full_url.endswith('json') else data)
    return open_url


@pytest.mark.parametrize('limit', [1024, 8 * 1024**3])
def test_manifest_above_limit_refused_before_tempfile_or_download(bundle, tmp_path, monkeypatch, limit):
    _, manifest = bundle[1]()
    manifest['files']['govtech.db']['bytes'] = limit + 1
    target = tmp_path / 'public.db'
    target.write_bytes(b'previous')

    def opener(request, timeout):
        assert request.full_url.endswith('/manifest.json'), 'DB response must not be opened'
        return Stream(json.dumps(manifest).encode())

    monkeypatch.setattr('govtech_scraper.railway_web.tempfile.NamedTemporaryFile',
                        lambda **kwargs: pytest.fail('must reject before tempfile'))
    options = {'max_bytes': limit} if limit < MAX_SNAPSHOT_BYTES else {}
    with pytest.raises(ValueError, match='maximum'):
        bootstrap(URL, target, opener=opener, **options)
    assert target.read_bytes() == b'previous'
    assert not list(tmp_path.glob('*.partial'))


@pytest.mark.parametrize('headroom', [-1, 0])
def test_exact_limit_and_disk_headroom(bundle, tmp_path, monkeypatch, headroom):
    data, manifest = bundle[1]()
    target = tmp_path / 'nested' / 'public.db'
    target.parent.mkdir()
    target.write_bytes(b'previous')
    checked = []

    def disk_usage(path):
        assert path == target.parent
        checked.append(path)
        return SimpleNamespace(free=len(data) + 256 * 1024**2 + headroom)

    monkeypatch.setattr('govtech_scraper.railway_web.shutil.disk_usage', disk_usage)
    base = transport(data, manifest)

    def opener(request, timeout):
        if request.full_url == URL:
            assert headroom == 0, 'DB response must not be opened'
            assert checked
        return base(request, timeout)

    if headroom < 0:
        monkeypatch.setattr('govtech_scraper.railway_web.tempfile.NamedTemporaryFile',
                            lambda **kwargs: pytest.fail('must reject before tempfile'))
        with pytest.raises(ValueError, match='free space'):
            bootstrap(URL, target, opener=opener, max_bytes=len(data))
        assert target.read_bytes() == b'previous'
    else:
        snapshot = bootstrap(URL, target, opener=opener, max_bytes=len(data))
        assert snapshot.path == target
        assert target.read_bytes() == data
    assert checked == [target.parent]
    assert not list(target.parent.glob('*.partial'))


def test_stream_overshoot_stops_immediately(bundle, tmp_path, monkeypatch):
    data, manifest = bundle[1]()
    target = tmp_path / 'public.db'
    target.write_bytes(b'previous')
    monkeypatch.setattr('govtech_scraper.railway_web.shutil.disk_usage',
                        lambda path: SimpleNamespace(free=len(data) + DISK_RESERVE_BYTES))

    class Overshoot(Stream):
        reads = 0

        def read(self, size=-1):
            self.reads += 1
            if self.reads == 1:
                return data[:1]
            if self.reads == 2:
                return data[1:] + b'x'
            pytest.fail('must stop immediately after overshoot')

    response = Overshoot()

    def opener(request, timeout):
        return Stream(json.dumps(manifest).encode()) if request.full_url.endswith('json') else response

    with pytest.raises(ValueError, match='exceeds advertised size'):
        bootstrap(URL, target, opener=opener, max_bytes=len(data))
    assert response.reads == 2
    assert response.closed
    assert target.read_bytes() == b'previous'
    assert not list(tmp_path.glob('*.partial'))


@pytest.mark.parametrize('limit', [True, False, None, '1024', 1.0, 0, -1, 8 * 1024**3 + 1])
def test_invalid_bootstrap_limit_before_io(tmp_path, monkeypatch, limit):
    target = tmp_path / 'public.db'
    target.write_bytes(b'previous')
    monkeypatch.setattr('govtech_scraper.railway_web.tempfile.NamedTemporaryFile',
                        lambda **kwargs: pytest.fail('must reject before tempfile'))
    with pytest.raises(ValueError, match='maximum'):
        bootstrap(URL, target, max_bytes=limit,
                  opener=lambda *a, **k: pytest.fail('must reject before network'))
    assert target.read_bytes() == b'previous'


@pytest.mark.parametrize('value', ['', 'true', 'false', '1.0', '1e3', '0', '-1',
                                   str(8 * 1024**3 + 1), ' 1024', '+1024'])
def test_invalid_runtime_limit(monkeypatch, value):
    monkeypatch.setenv('GOVTECH_WEB_MAX_DB_BYTES', value)
    monkeypatch.setenv('PORT', '8080')
    monkeypatch.setattr('govtech_scraper.railway_web.bootstrap',
                        lambda *a, **k: pytest.fail('must reject before bootstrap'))
    with pytest.raises(ValueError):
        main()


@pytest.mark.parametrize('value, expected', [(None, 8 * 1024**3), ('1024', 1024),
                                           (str(8 * 1024**3), 8 * 1024**3)])
def test_runtime_limit_forwarded(monkeypatch, value, expected):
    assert MAX_SNAPSHOT_BYTES == 8 * 1024**3
    assert DISK_RESERVE_BYTES == 256 * 1024**2
    monkeypatch.delenv('GOVTECH_WEB_MAX_DB_BYTES', raising=False)
    if value is not None:
        monkeypatch.setenv('GOVTECH_WEB_MAX_DB_BYTES', value)
    monkeypatch.setenv('PORT', '8080')
    calls = []
    monkeypatch.setattr('govtech_scraper.railway_web.bootstrap',
                        lambda *a, **k: calls.append(k))
    monkeypatch.setattr('govtech_scraper.railway_web.create_verified_app', lambda *a, **k: 'app')
    monkeypatch.setattr('uvicorn.run', lambda *a, **k: None)
    main()
    assert calls == [{'max_bytes': expected}]


def test_good_historical_snapshot_and_routes(bundle, tmp_path, monkeypatch):
    data, manifest = bundle[1]()
    monkeypatch.setenv('HF_TOKEN', 'never-send-this')
    snapshot = bootstrap(URL, tmp_path / 'public.db', opener=transport(data, manifest))
    app = create_verified_app(snapshot, source_commit='c' * 40)
    with TestClient(app) as client:
        health = client.get('/healthz')
        assert health.status_code == 200
        assert health.json()['healthy'] is True
        assert health.json()['dataset_sha'] == SHA
        assert health.json()['db_sha256'] == hashlib.sha256(data).hexdigest()
        assert health.json()['source_commit'] == 'c' * 40
        assert health.json()['scrape_timestamp'] == manifest['scrape_timestamp']
        assert health.json()['coverage'] == manifest['coverage']
        assert 'never-send-this' not in health.text
        assert client.get('/').status_code == 200
        assert client.get('/v1/stats').status_code == 200
        assert client.get('/api').json()['mcp'] == '/mcp/'


@pytest.mark.parametrize('damage', ['corrupt', 'truncated', 'oversize', 'counts', 'coverage',
    'scraped', 'schema', 'hash', 'bytes', 'timestamp', 'unverified', 'malformed', 'revision', 'partial'])
def test_failure_retains_snapshot(bundle, tmp_path, damage):
    data, manifest = bundle[1]()
    if damage == 'corrupt': data = b'x' + data[1:]
    if damage == 'truncated': data = data[:-1]
    if damage == 'oversize': data += b'x'
    if damage == 'counts': manifest['counts']['repositories'] += 1
    if damage == 'coverage': manifest['coverage']['complete'] = True
    if damage == 'scraped': manifest['scrape_timestamp'] = '2021-01-01T00:00:00Z'
    if damage == 'schema': manifest['schema_version'] = 2
    if damage == 'hash': manifest['files']['govtech.db']['sha256'] = '../bad'
    if damage == 'bytes': manifest['files']['govtech.db']['bytes'] = True
    if damage == 'timestamp': manifest['publication_timestamp'] = 'bad'
    if damage == 'unverified': manifest['export_verified'] = False
    opener = transport(data, manifest)
    if damage in ('malformed', 'revision', 'partial'):
        base = opener
        def opener(request, timeout):
            response = base(request, timeout)
            if damage == 'malformed': return Stream(b'{')
            if damage == 'revision': response.headers = {'X-Repo-Commit': 'd' * 40}
            if damage == 'partial': response.status = 206
            return response
    target = tmp_path / 'public.db'
    target.write_bytes(b'last good snapshot')
    with pytest.raises((ValueError, RuntimeError)):
        bootstrap(URL, target, opener=opener)
    assert target.read_bytes() == b'last good snapshot'
    assert not list(tmp_path.glob('*.partial'))


@pytest.mark.parametrize('table', ['openrouter_requests', 'worker_status', 'OPENROUTER_secret'])
def test_private_tables_rejected(bundle, tmp_path, table):
    with sqlite3.connect(bundle[0]) as conn:
        conn.execute(f'CREATE TABLE {table}(secret TEXT)')
        conn.commit()
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    data, manifest = bundle[1]()
    with pytest.raises(ValueError, match='private'):
        bootstrap(URL, tmp_path / 'public.db', opener=transport(data, manifest))


@pytest.mark.parametrize('url', ['', URL.replace(SHA, 'main'), URL.replace('https:', 'http:'),
    URL + '?token=secret', URL + '#fragment', URL.replace('huggingface.co', 'evil.test'),
    URL.replace('/data/', '/data/../data/'), URL.replace('/data/', '/%64ata/'),
    URL.replace('huggingface.co', 'user@huggingface.co'), URL.replace('govtech.db', '../govtech.db')])
def test_unsafe_source(url):
    with pytest.raises(ValueError): parse_source(url)


def test_main_binds_railway_port(monkeypatch):
    calls = []
    monkeypatch.setenv('GOVTECH_DB_URL', URL)
    monkeypatch.setenv('PORT', '8123')
    monkeypatch.setattr('govtech_scraper.railway_web.bootstrap', lambda *a, **k: 'snapshot')
    monkeypatch.setattr('govtech_scraper.railway_web.create_verified_app', lambda *a, **k: 'app')
    monkeypatch.setattr('uvicorn.run', lambda *a, **k: calls.append((a, k)))
    main()
    assert calls == [(('app',), {'host': '0.0.0.0', 'port': 8123})]


@pytest.mark.parametrize('url', ['http://huggingface.co/file', 'file:///etc/passwd',
    'https://127.0.0.1/file', 'https://evil.test/file', 'https://huggingface.co.evil.test/file',
    'https://user:secret@cdn.hf.co/file', 'https://cdn.hf.co:444/file'])
def test_unsafe_redirect(url):
    import urllib.request
    from govtech_scraper.railway_web import PublicRedirects
    with pytest.raises(ValueError):
        PublicRedirects().redirect_request(urllib.request.Request(URL), None, 302, '', {}, url)


def test_storage_redirect_allowed():
    import urllib.request
    from govtech_scraper.railway_web import PublicRedirects
    redirect = PublicRedirects().redirect_request(
        urllib.request.Request(URL), None, 302, '', {}, 'https://cas-bridge.xethub.hf.co/file?signature=x')
    assert redirect.full_url.startswith('https://cas-bridge.xethub.hf.co/')
    assert not redirect.has_header('Authorization')


def test_invalid_sqlite_with_matching_digest(bundle, tmp_path):
    _, manifest = bundle[1]()
    data = b'not a sqlite database'
    manifest['files']['govtech.db'] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    target = tmp_path / 'public.db'
    target.write_bytes(b'previous')
    with pytest.raises(sqlite3.DatabaseError):
        bootstrap(URL, target, opener=transport(data, manifest))
    assert target.read_bytes() == b'previous'
    assert not list(tmp_path.glob('*.partial'))


def test_interrupted_transfer(bundle, tmp_path):
    data, manifest = bundle[1]()
    base = transport(data, manifest)
    class Broken(Stream):
        def read(self, size=-1):
            raise OSError('transfer interrupted')
    def opener(request, timeout):
        return base(request, timeout) if request.full_url.endswith('json') else Broken(data)
    target = tmp_path / 'public.db'
    target.write_bytes(b'previous')
    with pytest.raises(OSError):
        bootstrap(URL, target, opener=opener)
    assert target.read_bytes() == b'previous'
    assert not list(tmp_path.glob('*.partial'))


def test_active_target_rejected(bundle, tmp_path):
    target = tmp_path / 'public.db'
    target.with_name('public.db-wal').touch()
    with pytest.raises(ValueError, match='offline'):
        bootstrap(URL, target, opener=lambda *a, **k: pytest.fail('must not download'))


def test_readiness_precedes_static_mount_without_database_reads(bundle, tmp_path, monkeypatch):
    from starlette.routing import Match
    data, manifest = bundle[1]()
    snapshot = bootstrap(URL, tmp_path / 'public.db', opener=transport(data, manifest))
    app = create_verified_app(snapshot, source_commit='c' * 40)
    scope = {'type': 'http', 'path': '/healthz', 'method': 'GET', 'root_path': ''}
    matches = [route for route in app.routes if route.matches(scope)[0] == Match.FULL]
    assert matches[0].path == '/healthz'
    assert any(getattr(route, 'name', '') == 'site' for route in app.routes)
    monkeypatch.setattr('sqlite3.connect', lambda *a, **k: pytest.fail('readiness must be cheap'))
    health = matches[0].endpoint()
    assert health['healthy'] is True
    assert health['dataset_sha'] == SHA
    assert health['scrape_timestamp'] == '2020-01-01T00:00:00+00:00'
    assert health['coverage'] == manifest['coverage']
