"""Private worker state: exact-revision transfers, guarded checkpoints, no implicit restore."""
from contextlib import contextmanager, closing
from datetime import UTC, datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import uuid

from .db import Database
from .operations import artifact_metadata, valid_sha, write_json
from .publication import connect_readonly, validate_database

PRIVATE_REPO = 'AndreasThinks/govtech-private-state'
MARKER = 'state/worker-marker.json'


def validate_master(path):
    if not Path(path).is_file():
        raise ValueError('Existing private master required; use explicit administrative recovery')
    validate_database(path)
    try:
        with closing(connect_readonly(path)) as conn:
            ceiling = conn.execute("SELECT value FROM openrouter_budget_config WHERE key='ceiling_usd'").fetchone()
            if not ceiling or not math.isfinite(float(ceiling[0])) or float(ceiling[0]) != 50:
                raise ValueError('Original $50 ceiling required')
            rows = conn.execute('SELECT request_hash,status,reserved_usd,actual_usd FROM openrouter_budget_ledger')
            for key, status, held, actual in rows:
                if (not key or status not in ('reserved', 'uncertain', 'completed', 'accounting_breach')
                        or not math.isfinite(held) or held < 0
                        or (actual is not None and (not math.isfinite(actual) or actual < 0))):
                    raise ValueError('Malformed private ledger')
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'openrouter_recurring_authorization' in tables:
                from .decision.period_budget import RecurringAuthorization
                for row in conn.execute('SELECT config_json FROM openrouter_recurring_authorization'):
                    RecurringAuthorization(**json.loads(row[0])).serialized()
    except (sqlite3.Error, TypeError, OverflowError) as exc:
        raise ValueError('Missing or malformed private ledger') from exc


class HubTransport:
    """Real HF SDK adapter; every private read checks privacy, revision and blob identity."""
    def __init__(self, *, api, download, repo=PRIVATE_REPO):
        if repo != PRIVATE_REPO:
            raise ValueError('Private-state repository is fixed')
        self.api, self.download, self.repo = api, download, repo

    def info(self, revision=None):
        info = self.api.repo_info(repo_id=self.repo, repo_type='dataset', **({'revision': revision} if revision else {}))
        if info.private is not True:
            raise ValueError('Private state requires private=true')
        valid_sha(info.sha)
        if revision and info.sha != revision:
            raise ValueError('Private revision mismatch')
        return info

    def read(self, revision, name):
        self.info(revision)
        entries = self.api.get_paths_info(repo_id=self.repo, repo_type='dataset', revision=revision, paths=[name])
        if len(entries) != 1 or entries[0].path != name:
            raise FileNotFoundError(name)
        path = Path(self.download(repo_id=self.repo, repo_type='dataset', revision=revision,
                                  filename=name, force_download=True))
        actual, entry = artifact_metadata(path), entries[0]
        digest_ok = (entry.lfs.sha256 == actual['sha256'] and entry.lfs.size == actual['bytes']) if entry.lfs else entry.blob_id == actual['git_blob']
        if entry.size != actual['bytes'] or not digest_ok:
            raise ValueError('Private transfer hash/size mismatch')
        return path

    def commit(self, parent, files):
        from huggingface_hub import CommitOperationAdd
        self.info(parent)
        expected = {name: artifact_metadata(path) for name, path in files.items()}
        try:
            result = self.api.create_commit(repo_id=self.repo, repo_type='dataset', parent_commit=parent,
                commit_message='Verified private worker checkpoint', operations=[
                    CommitOperationAdd(path_in_repo=name, path_or_fileobj=str(path)) for name, path in files.items()])
            revision = valid_sha(result.oid)
        except Exception:
            # A timeout may have committed. Accept only an exact readback of every byte.
            revision = self.info().sha
            if revision == parent:
                raise RuntimeError('Private commit unverified; retain dirty marker') from None
        for name, metadata in expected.items():
            if artifact_metadata(self.read(revision, name)) != metadata:
                raise ValueError('Private commit readback mismatch')
        return revision


class PrivateState:
    def __init__(self, hub, directory):
        self.hub = hub
        self.directory = Path(directory)
        self.master = self.directory / 'master.db'

    @contextmanager
    def lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / 'worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another worker writer holds the volume lock') from None
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def remote(self):
        revision = self.hub.info().sha
        try:
            marker = json.loads(self.hub.read(revision, MARKER).read_text())
        except FileNotFoundError:
            marker = None
        if marker is not None and (marker.get('schema_version') != 1 or not marker.get('state_id')
                                   or marker.get('initialized') is not True or type(marker.get('dirty')) is not bool):
            raise ValueError('Invalid private initialization marker')
        return revision, marker

    def write_marker(self, parent, marker):
        with tempfile.TemporaryDirectory(dir=self.directory) as folder:
            path = Path(folder) / 'marker.json'
            write_json(path, marker)
            return self.hub.commit(parent, {MARKER: path})

    def install(self, *, restore=False):
        if restore and self.master.exists():
            raise ValueError('Restore cannot replace an existing master or discard its holds; operator recovery required')
        parent, marker = self.remote()
        if not restore and (marker is not None or self.master.exists()):
            raise ValueError('Already initialized; bootstrap cannot reset state')
        if restore and marker is None:
            raise ValueError('Restore requires established remote identity')
        manifest = json.loads(self.hub.read(parent, 'state/manifest.json').read_text())
        try:
            if (manifest['schema_version'] != 1 or manifest['private_state'] is not True
                    or not re.fullmatch('[0-9a-f]{32}', manifest['state_id'])):
                raise ValueError()
            for field in ('source_publication', 'metadata_publication'):
                valid_sha(manifest[field])
            if datetime.fromisoformat(manifest['created_at']).tzinfo is None:
                raise ValueError()
            if marker and marker['state_id'] != manifest['state_id']:
                raise ValueError()
            expected = manifest['master']
            source = self.hub.read(parent, 'state/master.db')
            actual = artifact_metadata(source)
            if expected != {k: actual[k] for k in ('bytes', 'sha256')}:
                raise ValueError()
            validate_master(source)
            with closing(connect_readonly(source)) as conn:
                table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='openrouter_worker_state'").fetchone()
                if table:
                    row = conn.execute("SELECT value FROM openrouter_worker_state WHERE key='state_id'").fetchone()
                    if not row or json.loads(row[0]) != manifest['state_id']:
                        raise ValueError('Seed database identity differs from manifest')
            exclusions = json.loads(self.hub.read(parent, 'state/review-exclusions.json').read_text())
            if not isinstance(exclusions, list):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ValueError('Invalid private manifest, lineage or snapshot') from None
        if any(Path(str(self.master) + s).exists() for s in ('-wal', '-shm')) or self.master.is_symlink():
            raise ValueError('Restore requires an offline regular target')
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.directory) as folder:
            candidate = Path(folder) / 'master.db'
            shutil.copyfile(source, candidate)
            db = Database(str(candidate))
            try:
                db.conn.execute('CREATE TABLE IF NOT EXISTS openrouter_worker_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                from .cloud_worker import metadata_identity
                values = {'metadata_identity': metadata_identity(candidate), 'state_id': manifest['state_id'], 'increment': manifest['metadata_publication'],
                          'source_publication': manifest['source_publication'],
                          'inference_disabled': bool(marker and marker['dirty'])}
                db.conn.executemany('INSERT OR REPLACE INTO openrouter_worker_state VALUES (?,?)',
                                    [(k, json.dumps(v)) for k, v in values.items()])
                db.conn.execute('CREATE TABLE IF NOT EXISTS evidence_review_exclusions (html_url TEXT, taxonomy_version INTEGER, model TEXT, tag_id TEXT, reason TEXT, PRIMARY KEY(html_url,taxonomy_version,model,tag_id))')
                for item in exclusions:
                    db.conn.execute('INSERT OR IGNORE INTO evidence_review_exclusions VALUES (?,?,?,?,?)',
                        tuple(item[k] for k in ('html_url', 'taxonomy_version', 'model', 'tag_id', 'reason')))
                db.conn.commit()
            finally:
                db.close()
            validate_master(candidate)
            if marker is None:
                marker = {'schema_version': 1, 'initialized': True, 'state_id': manifest['state_id'], 'dirty': False}
                self.write_marker(parent, marker)
            with candidate.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(candidate, self.master)
            self._sync_directory()

    def _sync_directory(self):
        fd = os.open(self.directory, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def open_normal(self):
        validate_master(self.master)  # Never construct Database on missing/corrupt state.
        _, marker = self.remote()
        with closing(connect_readonly(self.master)) as conn:
            try:
                identity = json.loads(conn.execute("SELECT value FROM openrouter_worker_state WHERE key='state_id'").fetchone()[0])
            except (sqlite3.Error, TypeError):
                raise ValueError('Private master lacks initialized identity') from None
        if marker is None or identity != marker['state_id']:
            raise ValueError('Private master lineage mismatch')
        with closing(connect_readonly(self.master)) as conn:
            values = {r[0]: json.loads(r[1]) for r in conn.execute('SELECT key,value FROM openrouter_worker_state')}
        if not marker['dirty'] and marker.get('checkpoint_id') != values.get('checkpoint_id'):
            raise ValueError('Private checkpoint lineage mismatch; explicit recovery required')
        if (marker['dirty'] and not values.get('inference_disabled')
                and marker.get('run_token') != values.get('run_token')):
            raise ValueError('Private dirty run lineage mismatch')
        db = Database(str(self.master))
        db.conn.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def get(db, key, default=None):
        row = db.conn.execute('SELECT value FROM openrouter_worker_state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set(db, key, value):
        with db.conn:
            db.conn.execute('INSERT OR REPLACE INTO openrouter_worker_state VALUES (?,?)', (key, json.dumps(value)))

    def mark_dirty(self, db):
        if self.get(db, 'inference_disabled', True):
            raise ValueError('Restored dirty state requires operator recovery; inference disabled')
        parent, marker = self.remote()
        if marker['dirty'] and marker.get('run_token') != self.get(db, 'run_token'):
            raise ValueError('Remote dirty run does not belong to intact local ledger')
        token = uuid.uuid4().hex
        self.set(db, 'run_token', token)
        marker.update(dirty=True, run_token=token)
        self.write_marker(parent, marker)

    def checkpoint(self, db):
        parent, marker = self.remote()
        if marker['state_id'] != self.get(db, 'state_id'):
            raise ValueError('Checkpoint lineage mismatch')
        checkpoint_id = uuid.uuid4().hex
        self.set(db, 'checkpoint_id', checkpoint_id)
        with tempfile.TemporaryDirectory(dir=self.directory) as folder:
            folder = Path(folder)
            backup = folder / 'master.db'
            with closing(sqlite3.connect(backup)) as destination:
                db.conn.backup(destination)
            validate_master(backup)
            meta = artifact_metadata(backup)
            manifest = {'schema_version': 1, 'private_state': True,
                'state_id': marker['state_id'], 'master': {k: meta[k] for k in ('bytes', 'sha256')},
                'source_publication': self.get(db, 'source_publication'),
                'metadata_publication': self.get(db, 'increment'), 'created_at': datetime.now(UTC).isoformat()}
            write_json(folder / 'manifest.json', manifest)
            reviews = [dict(r) for r in db.conn.execute('SELECT * FROM evidence_review_exclusions')]
            write_json(folder / 'review-exclusions.json', reviews)
            revision = self.hub.commit(parent, {f'state/{name}': folder / name for name in ('master.db', 'manifest.json', 'review-exclusions.json')})
            # Never clear another/unknown run or a dirty restored lineage.
            if (not self.get(db, 'inference_disabled', True)
                    and (not marker['dirty'] or marker.get('run_token') == self.get(db, 'run_token'))):
                marker.update(dirty=False, checkpoint=revision, checkpoint_id=checkpoint_id)
                self.write_marker(revision, marker)
            return revision
