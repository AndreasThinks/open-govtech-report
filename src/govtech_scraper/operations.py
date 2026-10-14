"""Verified dataset publication and serialized, resumable Index rollout.

Callers must hold the shared publication lock. Network clients are injected so tests
exercise these operations without contacting Hugging Face or a model provider.
"""
import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from .publication import FILES, check_freshness, validate_bundle

DATASET = 'AndreasThinks/government-github-repos'
SPACE = 'AndreasThinks/govtech-index'
STATS_URL = 'https://www.govtechindex.cloud/v1/stats'
NATIVE_STATS_URL = 'https://andreasthinks-govtech-index.hf.space/v1/stats'
INTENT_KEY = 'GOVTECH_UPDATE_STATE'


def valid_sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('Expected exact dataset commit SHA')
    return value


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def artifact_metadata(path):
    path = Path(path)
    size = path.stat().st_size
    git = hashlib.sha1(b'blob ' + str(size).encode() + b'\0')
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            git.update(chunk)
            sha.update(chunk)
    return {'bytes': size, 'sha256': sha.hexdigest(), 'git_blob': git.hexdigest()}


def verify_remote(api, sha, manifest, artifacts):
    """Read the returned immutable revision, never the moving main branch."""
    valid_sha(sha)
    expected_paths = {f'data/{name}' for name in (*FILES, 'manifest.json')}
    if set(artifacts) != expected_paths:
        raise ValueError('Incomplete artifact receipt')
    infos = api.get_paths_info(repo_id=DATASET, repo_type='dataset',
                               revision=sha, paths=sorted(expected_paths))
    if {entry.path for entry in infos} != expected_paths or len(infos) != len(expected_paths):
        raise ValueError('Incomplete remote publication')
    for entry in infos:
        expected = artifacts[entry.path]
        if entry.size != expected['bytes']:
            raise ValueError('Remote artifact size mismatch')
        if entry.lfs:
            if entry.lfs.sha256 != expected['sha256'] or entry.lfs.size != expected['bytes']:
                raise ValueError('Remote LFS checksum mismatch')
        elif entry.blob_id != expected['git_blob']:
            raise ValueError('Remote Git checksum mismatch')
    remote = Path(api.hf_hub_download(repo_id=DATASET, repo_type='dataset',
                  revision=sha, filename='data/manifest.json', force_download=True))
    if json.loads(remote.read_text()) != manifest:
        raise ValueError('Remote manifest mismatch')


def publish_bundle(directory, api, *, parent_commit):
    from huggingface_hub import CommitOperationAdd
    from .api.queries import Catalogue
    directory = Path(directory)
    # An old success receipt must not survive a failed new attempt.
    (directory / 'receipt.json').unlink(missing_ok=True)
    manifest = validate_bundle(directory)
    check_freshness(manifest)
    valid_sha(parent_commit)
    artifacts = {f'data/{name}': artifact_metadata(directory / name)
                 for name in (*FILES, 'manifest.json')}
    operations = [CommitOperationAdd(path_in_repo=name,
                  path_or_fileobj=str(directory / Path(name).name)) for name in artifacts]
    operations.append(CommitOperationAdd(path_in_repo='README.md',
                                         path_or_fileobj=dataset_card(manifest).encode()))
    result = api.create_commit(repo_id=DATASET, repo_type='dataset', operations=operations,
        parent_commit=parent_commit,
        commit_message=f"Verified dataset {manifest['publication_timestamp']}")
    sha = valid_sha(result.oid)
    verify_remote(api, sha, manifest, artifacts)
    receipt = {'dataset': DATASET, 'dataset_sha': sha, 'parent_commit': parent_commit,
               'manifest': manifest, 'artifacts': artifacts,
               'stats': Catalogue(str(directory / 'govtech.db')).stats()}
    write_json(directory / 'receipt.json', receipt)
    return receipt


def read_stats(url):
    # Public endpoint: no HF token, query parameters or credential-bearing URLs.
    request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def update_index(receipt, api, *, fetch=read_stats, attempts=30, sleep=time.sleep,
                 allow_native_fallback=False):
    if not 1 <= attempts <= 60:
        raise ValueError('Poll attempts must be between 1 and 60')
    if receipt.get('dataset') != DATASET:
        raise ValueError('Unexpected dataset')
    sha = valid_sha(receipt['dataset_sha'])
    check_freshness(receipt['manifest'])
    verify_remote(api, sha, receipt['manifest'], receipt['artifacts'])
    url = f'https://huggingface.co/datasets/{DATASET}/resolve/{sha}/data/govtech.db'
    variables = api.get_space_variables(repo_id=SPACE)
    state = json.loads(variables[INTENT_KEY].value) if INTENT_KEY in variables else {}
    # Never overwrite an unresolved intent, even for a different publication.
    if state.get('phase') == 'intent':
        raise RuntimeError('Unresolved restart intent; operator review required')

    def set_variable(key, value):
        api.add_space_variable(repo_id=SPACE, key=key, value=value)
        observed = api.get_space_variables(repo_id=SPACE)
        if key not in observed or observed[key].value != value:
            raise RuntimeError('Space variable readback failed')

    def record(phase):
        set_variable(INTENT_KEY, json.dumps({'dataset_sha': sha, 'phase': phase}, sort_keys=True))

    same = state.get('dataset_sha') == sha and state.get('phase') in {'restarted', 'verified'}
    if same:
        if variables.get('GOVTECH_DB_URL') is None or variables['GOVTECH_DB_URL'].value != url:
            raise RuntimeError('Space database variable drift; operator review required')
    else:
        set_variable('GOVTECH_DB_URL', url)
        record('intent')
        api.restart_space(repo_id=SPACE)
        record('restarted')
    expected = receipt['stats']
    keys = ('repos', 'last_scraped', 'classified_repos')
    for attempt in range(attempts):
        try:
            actual = fetch(STATS_URL)
            if all(actual.get(key) == expected[key] for key in keys):
                if state.get('phase') != 'verified' or not same:
                    record('verified')
                return {'dataset_sha': sha, 'endpoint': STATS_URL, 'stats': actual}
        except Exception:
            pass  # Transport errors can contain credentials; do not log them.
        if attempt + 1 < attempts:
            sleep(10)
    if allow_native_fallback:
        # Diagnostic only: native success cannot certify the production domain.
        try:
            actual = fetch(NATIVE_STATS_URL)
            matches = all(actual.get(key) == expected[key] for key in keys)
        except Exception:
            matches = False
        print(f'Explicit native HF diagnostic: snapshot match={matches}; canonical verification still failed')
    raise RuntimeError('Index canonical stats verification failed within bounded wait')


def dataset_card(manifest):
    classification = manifest.get('classification')
    evidence_note = ''
    if classification:
        evidence_note = (f"\nEvidence model: {classification['model']}. "
            f"Labelled originals: {classification['labelled_repos']:,} / "
            f"{classification['current_total_repos']:,}.\n")
    return f'''---
license: agpl-3.0
configs:
  - config_name: default
    data_files: data/government_repos_latest.parquet
---
# Government GitHub Repositories

Public repositories from accounts listed in the government.github.com registry.
Registry coverage is not a census of government software. Weekly refreshes are scheduled;
see data/manifest.json for actual freshness and account coverage.

Repositories: {manifest['counts']['repositories']:,}
Accounts stored (including historical): {manifest['counts']['accounts']:,}
Countries stored: {manifest['counts']['countries']:,}
Latest repository scrape: {manifest['scrape_timestamp']}
Publication: {manifest['publication_timestamp']}
Source commit: {manifest['source_commit']}

CSV, Parquet, SQLite history, tag groups and memberships are published together.
CSV/Parquet contain repository metadata; evidence labels and source quotes are in SQLite
and the Index REST/MCP interfaces. Legacy tags are separate from evidence classifications.
Classification is AI-assisted, not human-certified or a calibrated probability. Coverage
is partial: records can be abstained, failed or pending. Literal quotes do not certify
semantic accuracy. See data/manifest.json for recorded classification coverage.
{evidence_note}
commit_count NULL and fork_source NULL mean unknown.
'''
