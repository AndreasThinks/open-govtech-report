"""Real adapters with fake HTTP/GraphQL and immutable local HF artifacts."""
import copy
import io
import json

import pytest

from tests.reliability.test_index_operations import bundle, Hub, publish
from tests.railway.test_worker import state, running_worker
from govtech_scraper import railway_rollout as rr
from govtech_scraper.operations import NATIVE_STATS_URL, STATS_URL

SHA = 'c' * 40
CODE = 'e' * 40
WEB = 'https://govtech-web.up.railway.app'


def env():
    return dict(GOVTECH_RAILWAY_ROLLOUT_ENABLED='true', GOVTECH_RAILWAY_PROJECT_ID='project',
        GOVTECH_RAILWAY_ENVIRONMENT_ID='environment', GOVTECH_RAILWAY_WEB_SERVICE_ID='web',
        GOVTECH_RAILWAY_WEB_URL=WEB, GOVTECH_RAILWAY_API_TOKEN='test-secret')


class Transport:
    def __init__(self):
        self.variables = {'RAILWAY_DOCKERFILE_PATH': 'spaces/railway/Dockerfile.web', 'OTHER': 'keep'}
        self.instance = {'source': {'repo': rr.PUBLIC_REPO, 'image': None}, 'volumeMounts': [],
                         'dockerfilePath': 'spaces/railway/Dockerfile.web',
                         'startCommand': None, 'cronSchedule': None,
                         'latestDeployment': {'id': 'old', 'status': 'SUCCESS',
                                              'meta': {'commitHash': CODE}}}
        self.writes = []
        self.ambiguous = False
        self.drop = False
        self.requests = []
        self.scope = {'projectId': 'project', 'environmentId': 'environment'}

    def __call__(self, request, timeout):
        self.requests.append(request)
        assert request.full_url == 'https://backboard.railway.com/graphql/v2'
        assert dict(request.header_items()) == {
            'Project-access-token': 'test-secret', 'Content-type': 'application/json'}
        assert 'test-secret' not in request.data.decode()
        payload = json.loads(request.data)
        query = payload['query']
        args = payload['variables']
        if 'projectToken' in query:
            assert query == 'query { projectToken { projectId environmentId } }'
            assert args == {}
            data = {'projectToken': self.scope}
        elif 'mutation' in query:
            change = args['input']
            assert change['replace'] is False
            assert (change['projectId'], change['environmentId'], change['serviceId']) == ('project', 'environment', 'web')
            self.writes.append(copy.deepcopy(change))
            if not self.drop:
                self.variables.update(change['variables'])
                if not change['skipDeploys']:
                    self.instance['latestDeployment']['id'] = 'new'
            if self.ambiguous:
                raise OSError('test-secret')
            data = {'variableCollectionUpsert': True}
        else:
            data = {'variables': self.variables, 'serviceInstance': self.instance}
        return io.BytesIO(json.dumps({'data': data}).encode())


@pytest.fixture
def setup(bundle):
    hub = Hub(bundle)
    receipt = publish(bundle, hub)
    transport = Transport()
    client = rr.RailwayClient(rr.RolloutConfig.from_env(env()), transport=transport)
    seen = []
    def fetch(url):
        seen.append(url)
        if url.endswith('/healthz'):
            return {'healthy': True, 'dataset_sha': SHA,
                    'db_sha256': receipt['artifacts']['data/govtech.db']['sha256'], 'source_commit': CODE}
        return receipt['stats']
    return hub, receipt, transport, client, fetch, seen


def test_default_and_combined(setup):
    hub, receipt, transport, client, fetch, seen = setup
    rr.update_public_index(receipt, hub, environ={}, fetch=fetch, attempts=1)
    assert seen == [STATS_URL]
    seen.clear()
    result = rr.update_public_index(receipt, hub, environ=env(), railway=client, fetch=fetch, attempts=1)
    assert seen == [NATIVE_STATS_URL, WEB + '/healthz', WEB + '/v1/stats', STATS_URL]
    assert result['canonical_verified'] is True
    assert result['railway']['deployment_id'] == 'new'
    assert transport.variables['OTHER'] == 'keep'
    assert [w['skipDeploys'] for w in transport.writes] == [True, False, True]
    assert 'test-secret' not in json.dumps(result)
    assert 'test-secret' not in json.dumps(transport.variables)
    assert 'test-secret' not in json.dumps(transport.writes)
    assert 'test-secret' not in repr(client.config)
    rr.update_public_index(receipt, hub, environ=env(), railway=client, fetch=fetch, attempts=1)
    assert len(transport.writes) == 3
    assert hub.restarts == 1


@pytest.mark.parametrize('key', list(env())[1:])
def test_missing_config(key):
    values = env()
    del values[key]
    with pytest.raises(ValueError):
        rr.RolloutConfig.from_env(values)


@pytest.mark.parametrize('url', ['http://govtech-web.up.railway.app', 'https://localhost',
    'https://user@govtech-web.up.railway.app', WEB+'?x=1', WEB+'#x', WEB+'/x',
    'https://govtech-web.up.railway.app.evil.test', 'https://127.0.0.1'])
def test_unsafe_url(url):
    with pytest.raises(ValueError):
        rr.RolloutConfig.from_env({**env(), 'GOVTECH_RAILWAY_WEB_URL': url})


@pytest.mark.parametrize('bad', ['repo', 'image', 'volume', 'worker', 'credential'])
def test_wrong_target(setup, bad):
    hub, receipt, t, client, fetch, _ = setup
    if bad == 'repo': t.instance['source']['repo'] = 'private/repo'
    if bad == 'image': t.instance['source']['image'] = 'worker:latest'
    if bad == 'volume': t.instance['volumeMounts'] = [{'mountPath': '/private'}]
    if bad == 'worker': t.variables['RAILWAY_DOCKERFILE_PATH'] = 'spaces/railway/Dockerfile.worker'
    if bad == 'credential': t.variables['HF_TOKEN'] = 'private'
    with pytest.raises(RuntimeError):
        rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert not t.writes


def test_ambiguous_applied_write_no_retry(setup):
    hub, receipt, t, client, fetch, _ = setup
    t.ambiguous = True
    rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert [w['skipDeploys'] for w in t.writes] == [True, False, True]


def test_readback_failure_sanitized(setup):
    hub, receipt, t, client, fetch, _ = setup
    t.drop = t.ambiguous = True
    with pytest.raises(RuntimeError, match='readback') as error:
        rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert 'test-secret' not in str(error.value)
    assert len(t.writes) == 1


def test_resume_without_redeploy_and_drift(setup):
    hub, receipt, t, client, fetch, _ = setup
    with pytest.raises(RuntimeError):
        rr.rollout_railway(receipt, hub, client, fetch=lambda _: {}, attempts=1)
    assert len(t.writes) == 2
    rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert len(t.writes) == 3
    t.variables['GOVTECH_DB_URL'] = 'drift'
    with pytest.raises(RuntimeError):
        rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert len(t.writes) == 3


@pytest.mark.parametrize('bad', ['health', 'source', 'stats', 'deployment'])
def test_bounded_failure(setup, bad):
    hub, receipt, t, client, fetch, _ = setup
    def wrong(url):
        value = copy.deepcopy(fetch(url))
        if bad == 'health': value['dataset_sha'] = '0'*40
        if bad == 'source': value['source_commit'] = receipt['manifest']['source_commit']
        if bad == 'stats': value['repos'] = -1
        if bad == 'deployment': t.instance['latestDeployment']['status'] = 'FAILED'
        return value
    sleeps = []
    with pytest.raises(RuntimeError):
        rr.rollout_railway(receipt, hub, client, fetch=wrong, attempts=2, sleep=sleeps.append)
    assert sleeps == [10]
    assert len(t.writes) == 2


def test_native_success_cannot_certify_canonical(setup):
    hub, receipt, t, client, fetch, _ = setup
    with pytest.raises(RuntimeError, match='canonical'):
        rr.update_public_index(receipt, hub, environ=env(), railway=client,
            fetch=lambda url: {} if url == STATS_URL else fetch(url), attempts=1)


def test_public_transport_no_auth_or_redirects():
    seen = []
    def opener(request, timeout):
        seen.append(request)
        return io.BytesIO(b'{}')
    rr.read_public(WEB+'/healthz', transport=opener)
    assert dict(seen[0].header_items()) == {'Cache-control': 'no-cache'}
    assert 'test-secret' not in repr(vars(seen[0]))
    with pytest.raises(ValueError):
        rr.read_public('https://evil.test/v1/stats', transport=opener)
    assert len(seen) == 1


@pytest.mark.parametrize('phase,revision', [('prepared', SHA), ('unknown', SHA), ('deploying', 'd'*40)])
def test_unresolved_intent_never_overwritten(setup, phase, revision):
    hub, receipt, t, client, fetch, _ = setup
    t.variables[rr.INTENT_KEY] = json.dumps({'phase': phase, 'dataset_sha': revision})
    with pytest.raises(RuntimeError):
        rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert not t.writes


def test_ambiguous_deploy_not_applied_never_resends(setup):
    hub, receipt, t, client, fetch, _ = setup
    original = t.__call__
    def transport(request, timeout):
        payload = json.loads(request.data)
        if payload['variables'].get('input', {}).get('skipDeploys') is False:
            t.drop = t.ambiguous = True
        return original(request, timeout)
    client.transport = transport
    with pytest.raises(RuntimeError, match='readback'):
        rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    with pytest.raises(RuntimeError, match='unresolved'):
        rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert len(t.writes) == 2


def test_public_redirect_denied():
    with pytest.raises(RuntimeError, match='Redirect'):
        rr.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test')


def test_unknown_enable_flag_fails_closed():
    with pytest.raises(ValueError):
        rr.RolloutConfig.from_env({'GOVTECH_RAILWAY_ROLLOUT_ENABLED': 'TRUE'})


def test_explicit_staging_does_not_claim_canonical(setup):
    hub, receipt, _, client, fetch, seen = setup
    result = rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert STATS_URL not in seen
    assert 'canonical_verified' not in result
    assert result['endpoint'] == WEB


def test_metadata_revision_after_cutover(setup):
    hub, receipt, t, client, fetch, seen = setup
    rr.update_public_index(receipt, hub, environ=env(), railway=client, fetch=fetch, attempts=1)
    # Same bytes but new immutable metadata publication; use the real HF verifier.
    revision = 'd'*40
    next_receipt = {**receipt, 'dataset_sha': revision}
    original_paths, original_download = hub.get_paths_info, hub.hf_hub_download
    hub.get_paths_info = lambda **kw: original_paths(**{**kw, 'revision': SHA})
    hub.hf_hub_download = lambda **kw: original_download(**{**kw, 'revision': SHA})
    original_transport = client.transport
    def transport(request, timeout):
        response = original_transport(request, timeout)
        if not json.loads(request.data)['variables'].get('input', {}).get('skipDeploys', True):
            t.instance['latestDeployment']['id'] = 'newer'
        return response
    client.transport = transport
    def next_fetch(url):
        value = fetch(url)
        return {**value, 'dataset_sha': revision} if url.endswith('/healthz') else value
    seen.clear()
    result = rr.update_public_index(next_receipt, hub, environ=env(), railway=client,
                                    fetch=next_fetch, attempts=1)
    assert result['dataset_sha'] == revision
    assert seen[0] == NATIVE_STATS_URL and seen[-1] == STATS_URL
    assert hub.restarts == 2
    assert [w['skipDeploys'] for w in t.writes].count(False) == 2


def test_worker_uses_shared_combined_entrypoint():
    from govtech_scraper import cloud_worker
    assert cloud_worker.update_index is rr.update_public_index


def test_worker_pending_receipt_runs_actual_adapters_without_provider_or_volume(setup, monkeypatch):
    from types import SimpleNamespace
    from govtech_scraper import cloud_worker
    hub, receipt, _, client, fetch, _ = setup
    pending = {'pending_rollout': receipt}
    state = SimpleNamespace(get=lambda db, key: pending.get(key),
                            set=lambda db, key, value: pending.update({key: value}))
    def forbidden(*args, **kwargs):
        pytest.fail('resume must not access private volume, publish, or buy inference')
    worker = cloud_worker.Worker(state, hub, sources=forbidden, prices=forbidden,
                                client=forbidden, volume_check=forbidden)
    monkeypatch.setattr(hub, 'create_commit', forbidden)
    monkeypatch.setattr(cloud_worker, 'update_index', lambda value, api:
        rr.update_public_index(value, api, environ=env(), railway=client, fetch=fetch, attempts=1))
    assert worker.publish(None, 'b'*40, rollout=True) == receipt
    assert pending['pending_rollout'] is None


def test_drift_in_unrelated_variables_or_deployment_blocks_resume(setup):
    hub, receipt, t, client, fetch, _ = setup
    rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    for change in ('variable', 'deployment'):
        if change == 'variable': t.variables['OTHER'] = 'changed'
        else:
            t.variables['OTHER'] = 'keep'
            t.instance['latestDeployment']['id'] = 'unexpected'
        with pytest.raises(RuntimeError, match='drift'):
            rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert len(t.writes) == 3


def test_finalization_failure_cannot_certify_success(setup):
    hub, receipt, t, client, fetch, _ = setup
    original = client.transport
    def transport(request, timeout):
        change = json.loads(request.data)['variables'].get('input', {})
        if change and 'verified' in change['variables'].get(rr.INTENT_KEY, ''):
            t.drop = True
        return original(request, timeout)
    client.transport = transport
    with pytest.raises(RuntimeError):
        rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=2, sleep=lambda _: None)
    assert len(t.writes) == 3


@pytest.mark.parametrize('field,value', [('dockerfilePath', 'spaces/railway/Dockerfile.worker'),
    ('startCommand', 'python -m govtech_scraper.cloud_worker'), ('cronSchedule', '0 * * * *')])
def test_actual_service_configuration_rejects_worker(setup, field, value):
    hub, receipt, t, client, fetch, _ = setup
    t.instance[field] = value
    with pytest.raises(RuntimeError, match='target'):
        rr.rollout_railway(receipt, hub, client, fetch=fetch, attempts=1)
    assert not t.writes


async def test_worker_run_pending_rollout_bypasses_inference(setup, running_worker, monkeypatch):
    from govtech_scraper import cloud_worker
    hub, receipt, _, client, fetch, _ = setup
    worker, _ = running_worker
    worker.public = hub
    db = worker.state.open_normal()
    worker.state.set(db, 'pending_rollout', receipt)
    before = db.conn.execute('SELECT COUNT(*) FROM openrouter_budget_ledger').fetchone()[0]
    db.close()
    def forbidden(*args, **kwargs):
        pytest.fail('pending rollout must precede metadata/source acquisition or inference')
    worker.merge = worker.volume_check = forbidden
    monkeypatch.setattr(cloud_worker, 'update_index', lambda value, api:
        rr.update_public_index(value, api, environ=env(), railway=client, fetch=fetch, attempts=1))
    result = await worker.run(mode='classify', publish=True, rollout=True)
    assert result['phase'] == 'rollout-completed'
    db = worker.state.open_normal()
    assert worker.state.get(db, 'pending_rollout') is None
    assert db.conn.execute('SELECT COUNT(*) FROM openrouter_budget_ledger').fetchone()[0] == before
    db.close()


def test_worker_cli_defers_provider_creation(monkeypatch, capsys):
    from govtech_scraper import cloud_worker
    from types import SimpleNamespace
    def forbidden(*args, **kwargs):
        pytest.fail('rollout resume must not construct a provider client')
    monkeypatch.setenv('GOVTECH_HF_TOKEN', 'fake-offline-token')
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setattr(cloud_worker, 'OpenRouterClient', forbidden)
    monkeypatch.setattr(cloud_worker, 'PrivateState', lambda *a: object())
    monkeypatch.setattr(cloud_worker, 'HubTransport', lambda **kw: object())
    class Worker:
        def __init__(self, *args, **kwargs):
            assert kwargs.get('client') is None
            assert callable(kwargs['client_factory'])
        async def run(self, **kwargs):
            return {'phase': 'rollout-completed'}
    monkeypatch.setattr(cloud_worker, 'Worker', Worker)
    # The CLI creates a coroutine; consume it with a local loop in the harness run.
    def run(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        pytest.fail('unexpected asynchronous work')
    monkeypatch.setattr(cloud_worker.asyncio, 'run', run)
    args = SimpleNamespace(mode='classify', publish=True, rollout=True, data_dir='unused',
        authorization_id=None, limit=1, source_limit=1, concurrency=1, deadline=1000, checkpoint_margin=100)
    cloud_worker.execute(args)
    assert 'rollout-completed' in capsys.readouterr().out


def test_project_token_header_and_scope_before_target():
    transport = Transport()
    client = rr.RailwayClient(rr.RolloutConfig.from_env(env()), transport=transport)
    client.inspect()
    client.write({'OTHER': 'keep'}, skip_deploys=True)
    queries = [json.loads(request.data)['query'] for request in transport.requests]
    assert 'projectToken' in queries[0]
    assert 'serviceInstance' in queries[1]
    assert 'projectToken' in queries[2]
    assert 'mutation' in queries[3]


@pytest.mark.parametrize('scope', [
    {'projectId': 'wrong', 'environmentId': 'environment'},
    {'projectId': 'project', 'environmentId': 'wrong'},
    None, {}, [], 'test-secret',
    {'projectId': 'project'},
    {'projectId': 'project', 'environmentId': None},
    {'projectId': ['project'], 'environmentId': 'environment'},
])
@pytest.mark.parametrize('operation', ['inspect', 'write'])
def test_invalid_project_token_scope_blocks_target(scope, operation):
    transport = Transport()
    transport.scope = scope
    client = rr.RailwayClient(rr.RolloutConfig.from_env(env()), transport=transport)
    with pytest.raises(RuntimeError, match='scope') as error:
        if operation == 'inspect':
            client.inspect()
        else:
            client.write({'OTHER': 'changed'}, skip_deploys=False)
    assert 'test-secret' not in str(error.value)
    assert len(transport.requests) == 1
    assert 'projectToken' in json.loads(transport.requests[0].data)['query']
    assert not transport.writes


@pytest.mark.parametrize('response', [
    b'not json', b'null', b'[]', b'{}', b'{"data": null}',
    b'{"data": {}}',
    b'{"errors": [{"message": "test-secret"}], "data": {}}',
])
def test_invalid_scope_response_sanitized(response):
    requests = []
    def transport(request, timeout):
        requests.append(request)
        return io.BytesIO(response)
    client = rr.RailwayClient(rr.RolloutConfig.from_env(env()), transport=transport)
    with pytest.raises(RuntimeError) as error:
        client.write({'OTHER': 'changed'}, skip_deploys=False)
    assert 'test-secret' not in str(error.value)
    assert len(requests) == 1
    assert 'projectToken' in json.loads(requests[0].data)['query']


def test_scope_mismatch_precedes_combined_side_effects(setup):
    hub, receipt, transport, client, fetch, seen = setup
    transport.scope['environmentId'] = 'wrong'
    with pytest.raises(RuntimeError, match='scope'):
        rr.update_public_index(receipt, hub, environ=env(), railway=client,
                               fetch=fetch, attempts=1)
    assert not transport.writes
    assert hub.restarts == 0
    assert seen == []
