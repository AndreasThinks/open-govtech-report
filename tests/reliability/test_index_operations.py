import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_metadata_workflow_has_no_paid_services():
    workflow = (ROOT / '.github/workflows/scrape-and-publish.yml').read_text()
    assert 'OPENROUTER_API_KEY' not in workflow
    assert 'scripts/update_index.py' in workflow
    assert 'Reject retired tagging request' in workflow
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        text = path.read_text()
        if 'pytest tests/' in text:
            assert 'uv sync --frozen --dev --extra api' in text


@pytest.fixture
def bundle(tmp_db, sample_repo, tmp_path):
    from datetime import datetime, timezone
    from govtech_scraper.publication import prepare_publication
    tmp_db.save_repositories([sample_repo])
    now = datetime.now(timezone.utc).isoformat()
    tmp_db.conn.execute('INSERT INTO scrape_runs VALUES (?,?,?,?,?,?)', ('run', now, now, 1, 1, 1))
    tmp_db.conn.execute('INSERT INTO account_fetches VALUES (?,?,?,?,?,?)', ('run', 'gov-org', 'succeeded', 1, None, now))
    tmp_db.conn.commit()
    directory = tmp_path / 'bundle'
    prepare_publication(tmp_db.db_path, directory, 'a' * 40)
    return directory


class Hub:
    def __init__(self, directory):
        self.directory = directory
        self.variables = {'GOVTECH_REF': NS(value='pinned-code'), 'OTHER': NS(value='keep')}
        self.restarts = 0
        self.bad = None

    def create_commit(self, **kwargs):
        assert kwargs['parent_commit'] == 'b' * 40
        self.commit = kwargs
        return NS(oid='c' * 40)

    def hf_hub_download(self, **kwargs):
        assert kwargs['revision'] == 'c' * 40
        return str(self.directory / Path(kwargs['filename']).name)

    def get_paths_info(self, **kwargs):
        assert kwargs['revision'] == 'c' * 40
        result = []
        for name in kwargs['paths']:
            path = self.directory / Path(name).name
            data = path.read_bytes()
            result.append(NS(path=name, size=len(data), lfs=None,
                blob_id=hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()))
        if self.bad == 'missing':
            return result[:-1]
        if self.bad == 'hash':
            result[0].blob_id = 'wrong'
        return result

    def get_space_variables(self, **kwargs):
        return self.variables.copy()

    def add_space_variable(self, key, value, **kwargs):
        self.variables[key] = NS(value=value)

    def restart_space(self, **kwargs):
        self.restarts += 1
        if self.bad == 'restart':
            raise RuntimeError('secret-in-error')


def publish(bundle, hub):
    from govtech_scraper.operations import publish_bundle
    return publish_bundle(bundle, hub, parent_commit='b' * 40)


def test_publish_receipt_and_guard(bundle):
    hub = Hub(bundle)
    receipt = publish(bundle, hub)
    assert receipt['dataset_sha'] == 'c' * 40
    assert receipt['stats']['repos'] == 1
    assert json.loads((bundle / 'receipt.json').read_text()) == receipt
    assert hub.commit['operations'][-1].path_in_repo == 'README.md'


@pytest.mark.parametrize('bad', ['missing', 'hash'])
def test_incomplete_remote_never_gets_receipt(bundle, bad):
    hub = Hub(bundle)
    hub.bad = bad
    with pytest.raises(ValueError):
        publish(bundle, hub)
    assert not (bundle / 'receipt.json').exists()


def test_index_pins_checks_and_restarts_only_once(bundle):
    from govtech_scraper.operations import update_index
    hub = Hub(bundle)
    receipt = publish(bundle, hub)
    requested = []
    def fetch(url):
        requested.append(url)
        return receipt['stats']
    update_index(receipt, hub, fetch=fetch, attempts=1)
    update_index(receipt, hub, fetch=fetch, attempts=1)
    assert hub.restarts == 1
    assert hub.variables['GOVTECH_DB_URL'].value.endswith('/resolve/' + 'c' * 40 + '/data/govtech.db')
    assert hub.variables['GOVTECH_REF'].value == 'pinned-code'
    assert hub.variables['OTHER'].value == 'keep'
    assert requested == ['https://www.govtechindex.cloud/v1/stats'] * 2


def test_ambiguous_restart_is_not_repeated(bundle):
    from govtech_scraper.operations import update_index
    hub = Hub(bundle)
    receipt = publish(bundle, hub)
    hub.bad = 'restart'
    with pytest.raises(RuntimeError):
        update_index(receipt, hub, fetch=lambda _: {}, attempts=1)
    hub.bad = None
    with pytest.raises(RuntimeError, match='intent'):
        update_index(receipt, hub, fetch=lambda _: {}, attempts=1)
    assert hub.restarts == 1


def test_wrong_stats_fail_including_classification(bundle):
    from govtech_scraper.operations import update_index
    hub = Hub(bundle)
    receipt = publish(bundle, hub)
    wrong = dict(receipt['stats'], classified_repos=99)
    with pytest.raises(RuntimeError, match='verification'):
        update_index(receipt, hub, fetch=lambda _: wrong, attempts=1)


def test_existing_publish_must_not_report_success_without_readback(bundle, monkeypatch):
    import importlib.util
    from unittest.mock import Mock
    spec = importlib.util.spec_from_file_location('publish_hf', ROOT / 'scripts/publish_hf.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('HF_TOKEN', 'test-secret')
    hub = Mock()
    hub.create_commit.return_value = NS(oid='c' * 40)
    hub.get_paths_info.return_value = []
    monkeypatch.setattr('huggingface_hub.HfApi', lambda **_: hub)
    (bundle / 'baseline.json').write_text(json.dumps({'dataset_sha': 'b' * 40}))
    with pytest.raises(ValueError):
        module.main(bundle)


def test_restore_pins_and_records_baseline(tmp_path, monkeypatch):
    from tests.reliability.test_regressions import script
    from unittest.mock import Mock
    module = script('download_db')
    monkeypatch.chdir(tmp_path)
    hub = Mock()
    hub.repo_info.return_value = NS(sha='b' * 40)
    monkeypatch.setattr('huggingface_hub.HfApi', lambda **_: hub)
    restore = Mock()
    monkeypatch.setattr(module, 'restore_database', restore)
    module.main()
    assert '/resolve/' + 'b' * 40 + '/data/govtech.db' in restore.call_args.args[0]
    assert json.loads(Path('baseline.json').read_text())['dataset_sha'] == 'b' * 40


def test_lfs_readback_and_manifest_mismatch(bundle):
    from govtech_scraper.operations import publish_bundle
    hub = Hub(bundle)
    original = hub.get_paths_info
    def lfs(**kwargs):
        entries = original(**kwargs)
        for entry in entries:
            data = (bundle / Path(entry.path).name).read_bytes()
            entry.lfs = NS(size=len(data), sha256=hashlib.sha256(data).hexdigest())
            entry.blob_id = 'pointer-hash'
        return entries
    hub.get_paths_info = lfs
    assert publish(bundle, hub)['dataset_sha'] == 'c' * 40
    hub.hf_hub_download = lambda **_: str(bundle / 'wrong-manifest.json')
    (bundle / 'wrong-manifest.json').write_text('{}')
    with pytest.raises(ValueError, match='manifest mismatch'):
        publish_bundle(bundle, hub, parent_commit='b' * 40)
    assert not (bundle / 'receipt.json').exists()


def test_parent_conflict_leaves_no_receipt(bundle):
    hub = Hub(bundle)
    def conflict(**kwargs):
        raise RuntimeError('parent revision changed')
    hub.create_commit = conflict
    with pytest.raises(RuntimeError):
        publish(bundle, hub)
    assert not (bundle / 'receipt.json').exists()


def test_update_requires_remote_readback_before_mutation(bundle):
    from govtech_scraper.operations import update_index
    hub = Hub(bundle)
    receipt = publish(bundle, hub)
    hub.bad = 'hash'
    with pytest.raises(ValueError):
        update_index(receipt, hub, attempts=1)
    assert hub.restarts == 0
    assert 'GOVTECH_DB_URL' not in hub.variables


def test_failed_variable_readback_never_restarts(bundle):
    from govtech_scraper.operations import update_index
    hub = Hub(bundle)
    receipt = publish(bundle, hub)
    hub.add_space_variable = lambda **_: None
    with pytest.raises(RuntimeError, match='readback'):
        update_index(receipt, hub, attempts=1)
    assert hub.restarts == 0


def test_bounded_wait_and_explicit_native_diagnostic(bundle, capsys):
    from govtech_scraper.operations import update_index, STATS_URL, NATIVE_STATS_URL
    hub = Hub(bundle)
    receipt = publish(bundle, hub)
    urls, sleeps = [], []
    def fetch(url):
        urls.append(url)
        if url == NATIVE_STATS_URL:
            return receipt['stats']
        raise RuntimeError('secret-do-not-log')
    with pytest.raises(RuntimeError, match='canonical'):
        update_index(receipt, hub, fetch=fetch, sleep=sleeps.append, attempts=2,
                     allow_native_fallback=True)
    assert urls == [STATS_URL, STATS_URL, NATIVE_STATS_URL]
    assert sleeps == [10]
    assert 'secret-do-not-log' not in capsys.readouterr().out
    # A completed restart followed by a failed health check is safely retryable.
    update_index(receipt, hub, fetch=lambda _: receipt['stats'], attempts=1)
    assert hub.restarts == 1


def test_classified_current_snapshot_preserves_history_and_tags(tmp_db, sample_repos, tmp_path):
    from datetime import datetime, timezone
    from govtech_scraper.publication import prepare_publication
    from govtech_scraper.decision.store import DecisionStore
    from govtech_scraper.decision.scorer import DecisionResult
    from govtech_scraper.decision import load_taxonomy
    tmp_db.save_repositories(sample_repos)
    tmp_db.save_repo_tag(sample_repos[0].html_url, 'messaging', .9, source='llm')
    store = DecisionStore(tmp_db)
    store.save([DecisionResult(html_url=r.html_url, input_hash='fixture', domain='00', kind='tool')
                for r in sample_repos], load_taxonomy().version, 'fixture-model')
    tmp_db.conn.execute('UPDATE repositories SET current=0 WHERE html_url=?', (sample_repos[0].html_url,))
    now = datetime.now(timezone.utc).isoformat()
    tmp_db.conn.execute('INSERT INTO scrape_runs VALUES (?,?,?,?,?,?)', ('run', now, now, 1, 1, 1))
    tmp_db.conn.execute('INSERT INTO account_fetches VALUES (?,?,?,?,?,?)', ('run', 'gov-org', 'succeeded', 2, None, now))
    tmp_db.conn.commit()
    directory = tmp_path / 'classified'
    prepare_publication(tmp_db.db_path, directory, 'a' * 40)
    hub = Hub(directory)
    receipt = publish(directory, hub)
    assert receipt['manifest']['counts']['repositories'] == 3
    assert receipt['manifest']['counts']['repository_snapshots'] == 3
    assert receipt['manifest']['counts']['repository_tags'] == 1
    assert receipt['stats']['repos'] == 2
    assert receipt['stats']['classified_repos'] == 2


def test_legacy_deploy_still_runs_if_index_health_check_fails():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/scrape-and-publish.yml').read_text())
    steps = workflow['jobs']['scrape']['steps']
    publish_step = next(s for s in steps if s['name'] == 'Publish to HuggingFace')
    deploy = next(s for s in steps if s['name'] == 'Deploy dashboard to HF Spaces')
    assert publish_step.get('id') == 'publish'
    assert deploy.get('if') == "always() && steps.publish.outcome == 'success'"
