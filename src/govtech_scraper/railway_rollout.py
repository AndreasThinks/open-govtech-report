"""Opt-in public Railway rollout. Callers hold the shared publication lock.

The two transports are deliberately separate: only GraphQL receives a token.
No private state, provider clients, CLI or credential discovery is used here.
"""
from dataclasses import dataclass, field
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .operations import (DATASET, STATS_URL, check_freshness,
                         valid_sha, verify_remote, update_index)

GRAPHQL_URL = 'https://backboard.railway.com/graphql/v2'
PUBLIC_REPO = 'AndreasThinks/open-govtech-report'
INTENT_KEY = 'GOVTECH_RAILWAY_ROLLOUT_STATE'
DB_KEY = 'GOVTECH_DB_URL'
KEYS = ('repos', 'last_scraped', 'classified_repos')


def public_url(url, *, base=False):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.query or parsed.fragment
            or not (host == 'www.govtechindex.cloud'
                    or host == 'andreasthinks-govtech-index.hf.space'
                    or re.fullmatch(r'[a-z0-9-]+\.up\.railway\.app', host))
            or parsed.path not in (('',) if base else ('/healthz', '/v1/stats'))
            or url != 'https://' + host + parsed.path):
        raise ValueError('Expected allowed public HTTPS endpoint')
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Redirect refused')


def _open(request, timeout):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(
        request, timeout=timeout)


def read_public(url, *, transport=None):
    public_url(url)
    try:
        with (transport or _open)(urllib.request.Request(url, headers={
                'Cache-Control': 'no-cache'}), timeout=20) as response:
            return json.loads(response.read(2 * 1024 * 1024))
    except Exception:
        raise RuntimeError('Public endpoint read failed') from None


@dataclass(frozen=True)
class RolloutConfig:
    project_id: str
    environment_id: str
    service_id: str
    web_url: str
    token: str = field(repr=False)

    @classmethod
    def from_env(cls, environ):
        flag = environ.get('GOVTECH_RAILWAY_ROLLOUT_ENABLED', '')
        if flag in ('', 'false'):
            return None
        if flag != 'true':
            raise ValueError('Railway enable flag must be explicitly true or false')
        names = ('PROJECT_ID', 'ENVIRONMENT_ID', 'WEB_SERVICE_ID', 'WEB_URL', 'API_TOKEN')
        values = [environ.get('GOVTECH_RAILWAY_' + name, '') for name in names]
        if any(not value or value.strip() != value or '\n' in value or '\r' in value
               for value in values):
            raise ValueError('Missing or invalid explicit Railway configuration')
        if any(not re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in values[:3]):
            raise ValueError('Invalid Railway target IDs')
        public_url(values[3], base=True)
        return cls(*values)

    def target(self):
        return dict(projectId=self.project_id, environmentId=self.environment_id,
                    serviceId=self.service_id)


class RailwayClient:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.transport = transport or _open

    def call(self, query, variables):
        scope = self._request('query { projectToken { projectId environmentId } }', {}).get(
            'projectToken')
        if (not isinstance(scope, dict)
                or scope.get('projectId') != self.config.project_id
                or scope.get('environmentId') != self.config.environment_id):
            raise RuntimeError('Railway project token scope mismatch or invalid response')
        return self._request(query, variables)

    def _request(self, query, variables):
        request = urllib.request.Request(GRAPHQL_URL,
            data=json.dumps(dict(query=query, variables=variables)).encode(),
            headers={'Project-Access-Token': self.config.token,
                     'Content-Type': 'application/json',
                     'User-Agent': 'govtech-index/1.0'})
        try:
            with self.transport(request, timeout=20) as response:
                result = json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'Railway API HTTP {exc.code}') from None
        except Exception as exc:
            # Exception text can contain request URLs or credentials. Expose only its type.
            raise RuntimeError(f'Railway API transport failed ({type(exc).__name__})') from None
        if not isinstance(result, dict):
            raise RuntimeError('Railway API returned a malformed response')
        errors = result.get('errors')
        if errors:
            messages = []
            for item in errors[:3] if isinstance(errors, list) else []:
                if isinstance(item, dict) and isinstance(item.get('message'), str):
                    message = item['message'][:240]
                    if self.config.token:
                        message = message.replace(self.config.token, '[REDACTED]')
                    messages.append(message)
            detail = '; '.join(messages) or 'GraphQL errors returned'
            raise RuntimeError(f'Railway GraphQL error: {detail}')
        if not isinstance(result.get('data'), dict):
            raise RuntimeError('Railway API response missing data')
        return result['data']

    def inspect(self):
        result = self.call('''query($projectId:String!,$environmentId:String!,$serviceId:String!){
          variables(projectId:$projectId,environmentId:$environmentId,serviceId:$serviceId)
          serviceInstance(serviceId:$serviceId,environmentId:$environmentId){
            source{repo image} dockerfilePath startCommand cronSchedule
            latestDeployment{id status meta}
          }
          project(id:$projectId){volumes{
            pageInfo{hasNextPage}
            edges{node{id volumeInstances{
              pageInfo{hasNextPage}
              edges{node{serviceId environmentId mountPath}}
            }}}
          }}
        }''', self.config.target())
        try:
            variables, instance = result['variables'], result['serviceInstance']
            self._check_volumes(result['project']['volumes'])
            source = instance['source']
            deployment = instance['latestDeployment']
            meta = deployment['meta']
            if isinstance(meta, str):
                meta = json.loads(meta)
            code = valid_sha(meta['commitHash'])
            if (source != {'repo': PUBLIC_REPO, 'image': None}
                    or instance['dockerfilePath'] != 'spaces/railway/Dockerfile.web'
                    or instance['startCommand'] not in (None, '', 'python -m govtech_scraper.railway_web')
                    or instance['cronSchedule'] not in (None, '')
                    or variables.get('RAILWAY_DOCKERFILE_PATH', 'spaces/railway/Dockerfile.web')
                        != 'spaces/railway/Dockerfile.web'
                    or variables.get('RAILWAY_RUN_COMMAND')
                    or variables.get('GOVTECH_WEB_DB', '/app/data/govtech.db') != '/app/data/govtech.db'
                    or not deployment['id']):
                raise ValueError()
            # Railway injects this hostname; exempt only this exact, validated key.
            if 'RAILWAY_PRIVATE_DOMAIN' in variables:
                domain = variables['RAILWAY_PRIVATE_DOMAIN']
                if (not isinstance(domain, str) or len(domain) > 253
                        or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.railway\.internal', domain)):
                    raise ValueError()
            # Reject credentials/private-worker configuration even if inherited from shared vars.
            if any(re.search(r'TOKEN|SECRET|PASSWORD|PRIVATE|OPENROUTER|GITHUB_APP|INSTALLATION|WORKER|VOLUME|API_KEY|ACCESS_KEY|CREDENTIAL',
                             key, re.I) for key in variables if key != 'RAILWAY_PRIVATE_DOMAIN'):
                raise ValueError()
            return variables, deployment, code
        except Exception:
            raise RuntimeError('Unsafe or unverifiable Railway public web target') from None

    def _check_volumes(self, volumes):
        # Never infer absence from a truncated page or an unknown/pending identity.
        for volume in _complete_connection_nodes(volumes):
            _volume_identity(volume['id'])
            for attachment in _complete_connection_nodes(volume['volumeInstances']):
                service = _volume_identity(attachment['serviceId'])
                environment = _volume_identity(attachment['environmentId'])
                mount = attachment['mountPath']
                if (not isinstance(mount, str) or not mount.startswith('/')
                        or mount.strip() != mount or any(ord(c) < 32 for c in mount)):
                    raise ValueError('Unverifiable volume mount path')
                if (service == self.config.service_id
                        and environment == self.config.environment_id):
                    raise ValueError('Public web volume attachment refused')

    def write(self, changes, *, skip_deploys):
        self.call('''mutation($input:VariableCollectionUpsertInput!){
          variableCollectionUpsert(input:$input)
        }''', {'input': {**self.config.target(), 'variables': changes,
                        'replace': False, 'skipDeploys': skip_deploys}})


def _complete_connection_nodes(connection):
    if (not isinstance(connection, dict)
            or not isinstance(connection.get('pageInfo'), dict)
            or connection['pageInfo'].get('hasNextPage') is not False
            or not isinstance(connection.get('edges'), list)):
        raise ValueError('Incomplete or unverifiable volume connection')
    for edge in connection['edges']:
        if not isinstance(edge, dict) or not isinstance(edge.get('node'), dict):
            raise ValueError('Unverifiable volume connection node')
        yield edge['node']


def _volume_identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Unverifiable volume identity')
    return value


def _fingerprint(variables):
    stable = {k: v for k, v in variables.items() if k not in (INTENT_KEY, DB_KEY)}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def _encode(intent):
    return json.dumps(intent, sort_keys=True)


def _checked_write(client, changes, *, skip_deploys):
    # A timeout may mean accepted. Always read back once; never resend the mutation.
    try:
        client.write(changes, skip_deploys=skip_deploys)
    except RuntimeError:
        pass
    variables, deployment, code = client.inspect()
    if any(variables.get(key) != value for key, value in changes.items()):
        raise RuntimeError('Railway write readback unresolved; operator review required')
    return variables, deployment, code


def rollout_railway(receipt, api, client, *, fetch=read_public, attempts=30, sleep=time.sleep):
    if type(attempts) is not int or not 1 <= attempts <= 60:
        raise ValueError('Poll attempts must be between 1 and 60')
    if receipt.get('dataset') != DATASET:
        raise ValueError('Unexpected dataset')
    sha = valid_sha(receipt['dataset_sha'])
    check_freshness(receipt['manifest'])
    verify_remote(api, sha, receipt['manifest'], receipt['artifacts'])
    digest = receipt['artifacts']['data/govtech.db']['sha256']
    if digest != receipt['manifest']['files']['govtech.db']['sha256']:
        raise ValueError('Receipt database checksum mismatch')
    pin = f'https://huggingface.co/datasets/{DATASET}/resolve/{sha}/data/govtech.db'
    variables, deployment, code = client.inspect()
    try:
        state = json.loads(variables.get(INTENT_KEY, '{}'))
        if not isinstance(state, dict):
            raise ValueError()
    except Exception:
        raise RuntimeError('Malformed Railway intent') from None
    fingerprint = _fingerprint(variables)
    target = client.config.target()
    same = state.get('dataset_sha') == sha
    # A verified earlier dataset is settled: ordinary code deploys of the web
    # service since then change its commit and deployment and are not drift.
    settled = not same and state.get('phase') == 'verified'
    if state:
        if (state.get('phase') not in ('deploying', 'verified')
                or (not same and state['phase'] != 'verified')
                or state.get('target') != target
                or state.get('web_url') != client.config.web_url
                or state.get('variables_hash') != fingerprint
                or (state.get('source_commit') != code and not settled)):
            raise RuntimeError('Railway intent unresolved or target drift; operator review required')
        previous_pin = f"https://huggingface.co/datasets/{DATASET}/resolve/{valid_sha(state['dataset_sha'])}/data/govtech.db"
        if variables.get(DB_KEY) != previous_pin:
            raise RuntimeError('Railway database variable drift')
        if same and state.get('db_sha256') != digest:
            raise RuntimeError('Railway receipt drift')
        if same and state['phase'] == 'verified' and state.get('deployment_id') != deployment['id']:
            raise RuntimeError('Railway deployment drift')
    if not same:
        state = dict(dataset_sha=sha, db_sha256=digest, phase='prepared', target=target,
                     web_url=client.config.web_url, source_commit=code, variables_hash=fingerprint,
                     previous_deployment_id=deployment['id'])
        observed, _, _ = _checked_write(client, {INTENT_KEY: _encode(state)}, skip_deploys=True)
        if _fingerprint(observed) != fingerprint:
            raise RuntimeError('Railway variable drift')
        state['phase'] = 'deploying'
        _checked_write(client, {DB_KEY: pin, INTENT_KEY: _encode(state)}, skip_deploys=False)
    for attempt in range(attempts):
        try:
            variables, deployment, deployed_code = client.inspect()
            if (variables.get(DB_KEY) != pin or variables.get(INTENT_KEY) != _encode(state)
                    or _fingerprint(variables) != fingerprint or deployed_code != code
                    or (state['phase'] == 'verified'
                        and state['deployment_id'] != deployment['id'])):
                raise RuntimeError('Railway config drift')
            health = fetch(client.config.web_url + '/healthz')
            stats = fetch(client.config.web_url + '/v1/stats')
            after_variables, after_deployment, after_code = client.inspect()
            if (after_variables != variables or after_deployment != deployment
                    or after_code != deployed_code):
                raise RuntimeError('Railway changed during readiness verification')
            if (deployment['status'] == 'SUCCESS'
                    and deployment['id'] != state['previous_deployment_id']
                    and health.get('healthy') is True and health.get('dataset_sha') == sha
                    and health.get('db_sha256') == digest and health.get('source_commit') == deployed_code
                    and all(stats.get(k) == receipt['stats'][k] for k in KEYS)):
                if state['phase'] != 'verified':
                    state.update(phase='verified', deployment_id=deployment['id'])
                    observed, final_deployment, final_code = _checked_write(
                        client, {INTENT_KEY: _encode(state)}, skip_deploys=True)
                    if (_fingerprint(observed) != fingerprint or observed.get(DB_KEY) != pin
                            or final_deployment != deployment or final_code != code):
                        raise RuntimeError('Railway finalization drift')
                return dict(dataset_sha=sha, target=target, endpoint=client.config.web_url,
                            deployment_id=deployment['id'], source_commit=code,
                            readiness=health, stats=stats, verified=True)
        except Exception:
            pass
        if attempt + 1 < attempts:
            sleep(10)
    raise RuntimeError('Railway readiness/deployment verification failed within bounded wait')


def update_public_index(receipt, api, *, environ=None, railway=None, fetch=read_public,
                        attempts=30, sleep=time.sleep, allow_native_fallback=False):
    """Default HF-only; enabled means Railway + canonical. The retired HF Space is not touched."""
    config = RolloutConfig.from_env(os.environ if environ is None else environ)
    if config is None:
        return update_index(receipt, api, fetch=fetch, attempts=attempts, sleep=sleep,
                            allow_native_fallback=allow_native_fallback)
    client = railway or RailwayClient(config)
    if client.config != config:
        raise ValueError('Railway adapter target differs from explicit configuration')
    result = rollout_railway(receipt, api, client, fetch=fetch, attempts=attempts, sleep=sleep)
    for attempt in range(attempts):
        try:
            stats = fetch(STATS_URL)
            if all(stats.get(k) == receipt['stats'][k] for k in KEYS):
                return dict(dataset_sha=receipt['dataset_sha'], endpoint=STATS_URL, stats=stats,
                            railway=result, canonical_verified=True)
        except Exception:
            pass
        if attempt + 1 < attempts:
            sleep(10)
    raise RuntimeError('Index canonical stats verification failed within bounded wait')
