"""Fail-closed retention of completed publication bundles on the worker volume.

A bundle is deleted only when every proof holds: a durable completion record made
by Worker.publish (or adopted from its own receipt), byte-identical local files,
the same artifacts at the recorded immutable public revision, and a clean private
checkpoint taken after the record. Everything else on the volume is preserved.
Callers hold the worker volume lock; nothing here acquires it or writes remotely.
"""
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .operations import DATASET, failure_reason, valid_sha, verify_remote
from .publication import FILES

STATE_KEY = 'publication_bundles'
RECEIPTS = 'retention-receipts.jsonl'
BUNDLE = re.compile(r'publication-[0-9a-f]{32}')
ARTIFACTS = (*FILES, 'manifest.json')
# Read-only SQLite connections to the WAL-mode snapshot leave these behind.
WAL, SHM = 'govtech.db-wal', 'govtech.db-shm'
ALLOWED = (*ARTIFACTS, 'receipt.json', WAL, SHM)
PROTECTED = ('master.db', 'master.db-wal', 'master.db-shm', 'worker.lock', RECEIPTS)
MAX_KEEP = 10
HISTORY = 50  # Deleted/missing records kept in state; the receipt log keeps all.
MAX_JSON = 16 * 1024 * 1024


class UnknownContents(ValueError):
    pass


def _now():
    return datetime.now(UTC).isoformat()


def _checked_artifacts(artifacts):
    if not isinstance(artifacts, dict) or set(artifacts) != {f'data/{name}' for name in ARTIFACTS}:
        raise ValueError('Incomplete artifact receipt')
    result = {}
    for path, meta in artifacts.items():
        if (not isinstance(meta, dict) or type(meta.get('bytes')) is not int or meta['bytes'] < 0
                or not re.fullmatch('[0-9a-f]{64}', str(meta.get('sha256')))
                or not re.fullmatch('[0-9a-f]{40}', str(meta.get('git_blob')))):
            raise ValueError('Malformed artifact receipt')
        result[path] = {key: meta[key] for key in ('bytes', 'sha256', 'git_blob')}
    return result


def _published(value):
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError('Publication timestamp lacks an offset')
    return moment


def _record(receipt, *, source, checkpoint_id):
    manifest = receipt['manifest']
    _published(manifest['publication_timestamp'])
    return {'status': 'completed', 'source': source, 'dataset_sha': valid_sha(receipt['dataset_sha']),
            'artifacts': _checked_artifacts(receipt['artifacts']),
            'published_at': manifest['publication_timestamp'], 'recorded_at': _now(),
            'checkpoint_before': checkpoint_id}


def record_completed(state, db, folder, receipt):
    """Durably note a verified publication. False means the bundle is never pruned."""
    try:
        folder = Path(folder)
        if folder.parent != state.directory or not BUNDLE.fullmatch(folder.name):
            return False
        if not stat.S_ISDIR(os.lstat(folder).st_mode):
            return False
        record = _record(receipt, source='publish', checkpoint_id=state.get(db, 'checkpoint_id'))
    except (KeyError, TypeError, ValueError, OSError):
        return False
    records = state.get(db, STATE_KEY, {})
    existing = records.get(folder.name, record) if isinstance(records, dict) else None
    # Never reopen a bundle whose deletion has started or finished.
    if not isinstance(existing, dict) or existing.get('status') != 'completed':
        return False
    records[folder.name] = record
    state.set(db, STATE_KEY, records)
    return True


def _contents(fd):
    """Exact, link-free file set of an open bundle directory: name -> bytes."""
    files = {}
    for entry in os.listdir(fd):
        info = os.lstat(entry, dir_fd=fd)
        if entry not in ALLOWED or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise UnknownContents('Bundle holds an unknown entry, link or non-regular file')
        if entry == WAL and info.st_size:
            # Frames absent from the published main file exist nowhere else.
            raise UnknownContents('Bundle snapshot has an uncheckpointed write-ahead log')
        files[entry] = info.st_size
    return files


def _open_bundle(root_fd, name):
    """Open a direct child directory without following links or crossing filesystems."""
    if not BUNDLE.fullmatch(name):
        raise UnknownContents('Not a publication bundle name')
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    if os.fstat(fd).st_dev != os.fstat(root_fd).st_dev:
        os.close(fd)
        raise UnknownContents('Bundle is on a different filesystem')
    return fd


def _read(fd, name, *, keep=False):
    """Hash one regular file relative to the open bundle; optionally return its bytes."""
    with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or (keep and info.st_size > MAX_JSON):
            raise UnknownContents('Unexpected bundle file')
        git = hashlib.sha1(b'blob ' + str(info.st_size).encode() + b'\0')
        sha, total, data = hashlib.sha256(), 0, []
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            git.update(chunk)
            sha.update(chunk)
            total += len(chunk)
            if keep:
                data.append(chunk)
        if total != info.st_size:
            raise ValueError('Bundle file changed while reading')
    return {'bytes': total, 'sha256': sha.hexdigest(), 'git_blob': git.hexdigest()}, b''.join(data)


def _verify(fd, record, public):
    """Local bytes equal the record and the record's immutable public revision."""
    files = _contents(fd)
    if not set(ARTIFACTS) <= set(files):
        raise ValueError('Bundle is missing published artifacts')
    sha = valid_sha(record.get('dataset_sha'))
    artifacts = _checked_artifacts(record.get('artifacts'))
    manifest = None
    for name in ARTIFACTS:
        meta, data = _read(fd, name, keep=name == 'manifest.json')
        if meta != artifacts[f'data/{name}']:
            raise ValueError('Local artifact differs from publication record')
        if name == 'manifest.json':
            manifest = json.loads(data)
    verify_remote(public, sha, manifest, artifacts)
    return files


def _inspect(root_fd, name):
    """Read-only view of one bundle: (state or None when healthy, reason, bytes, files)."""
    try:
        info = os.lstat(name, dir_fd=root_fd)
    except FileNotFoundError:
        return 'missing', 'Bundle directory is absent', 0, {}
    if stat.S_ISLNK(info.st_mode):
        return 'rejected-symlink', 'Bundle path is a symbolic link', info.st_size, {}
    try:
        fd = _open_bundle(root_fd, name)
        try:
            files = _contents(fd)
        finally:
            os.close(fd)
    except (UnknownContents, OSError) as exc:
        return 'unknown-contents', failure_reason(exc), _tree_bytes(root_fd, name), {}
    return None, None, sum(files.values()), files


def _tree_bytes(root_fd, name):
    """Apparent size without following links; None when it cannot be measured."""
    try:
        info = os.lstat(name, dir_fd=root_fd)
        if not stat.S_ISDIR(info.st_mode):
            return info.st_size
        total = 0
        for _, _, files, fd in os.fwalk(name, dir_fd=root_fd, follow_symlinks=False):
            total += sum(os.lstat(entry, dir_fd=fd).st_size for entry in files)
        return total
    except OSError:
        return None


def blockers(state, db):
    """Volume-wide reasons to delete nothing, plus the verified private marker."""
    _, marker = state.remote()
    reasons = []
    if marker is None or marker['state_id'] != state.get(db, 'state_id'):
        reasons.append('Private state marker missing or of another lineage')
    else:
        if marker['dirty']:
            reasons.append('Private marker is dirty; an interrupted or running job is unresolved')
        try:
            valid_sha(marker.get('checkpoint'))
            clean = bool(marker.get('checkpoint_id')) and marker['checkpoint_id'] == state.get(db, 'checkpoint_id')
        except ValueError:
            clean = False
        if not clean:
            reasons.append('No clean private checkpoint matches this volume')
    if state.get(db, 'inference_disabled', True):
        reasons.append('Restored state awaits operator recovery')
    if state.get(db, 'pending_publication'):
        reasons.append('Unresolved pending publication intent')
    return reasons, marker or {}


def _pending_name(state, db):
    pending = state.get(db, 'pending_publication')
    try:
        folder = Path(pending['directory'])
    except (KeyError, TypeError):
        return None
    return folder.name if folder.parent == state.directory else None


def _require_lock(state):
    if not getattr(state, 'locked', False):
        raise RuntimeError('Retention requires the held worker volume lock')


def _bounded(records):
    """Keep every live record and only the most recent finished ones."""
    done = [name for name, record in records.items() if record.get('status') in ('deleted', 'missing')]
    for name in done[:-HISTORY]:
        del records[name]
    return records


def retain(state, db, public, *, keep=3, apply=False, max_delete=10, clock=time.monotonic, deadline=None):
    """Inventory the volume and, with apply, delete proven bundles beyond the recent window."""
    if type(keep) is not int or not 1 <= keep <= MAX_KEEP:
        raise ValueError(f'Retention window must be an integer from 1 to {MAX_KEEP}')
    if type(max_delete) is not int or max_delete < 1:
        raise ValueError('Retention deletion bound must be a positive integer')
    _require_lock(state)
    reasons, marker = blockers(state, db)
    records = state.get(db, STATE_KEY, {})
    if not isinstance(records, dict) or not all(isinstance(r, dict) for r in records.values()):
        reasons.append('Malformed retention records')
        records = {}
    report = dict(phase='applied' if apply else 'dry-run', keep=keep, blockers=reasons, bundles=[], other=[],
                  rejected_records=sorted(repr(name)[:80] for name in records if not BUNDLE.fullmatch(name)),
                  missing=[], deleted=[], bytes_freed=0, bytes_reclaimable=0)
    pending = _pending_name(state, db)
    pins = {'pending-rollout': (state.get(db, 'pending_rollout') or {}).get('dataset_sha'),
            'last-publication': (state.get(db, 'last_publication') or {}).get('dataset_sha')}
    checkpoint_id = state.get(db, 'checkpoint_id')
    log = None
    root_fd = os.open(state.directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        present = sorted(os.listdir(root_fd))
        for name in present:
            if not BUNDLE.fullmatch(name):
                report['other'].append({'name': name, 'state': 'protected' if name in PROTECTED else 'unknown',
                                        'bytes': _tree_bytes(root_fd, name), 'action': 'preserved'})
        items, resumable, candidates = {}, [], []
        for name in sorted({n for n in present if BUNDLE.fullmatch(n)}
                           | {n for n, r in records.items() if BUNDLE.fullmatch(n)
                              and r.get('status') in ('completed', 'deleting')}):
            problem, reason, size, files = _inspect(root_fd, name)
            record = records.get(name)
            item = {'name': name, 'bytes': size, 'action': 'preserved',
                    'dataset_sha': record.get('dataset_sha') if record else None}
            if problem == 'missing':
                if record['status'] == 'deleting':
                    resumable.append(name)
                    item.update(state='deleting', reason='Interrupted deletion; directory already removed')
                    items[name] = item
                else:
                    report['missing'].append(name)
                continue
            items[name] = item
            if problem:
                item.update(state=problem, reason=reason)
            elif name == pending:
                item.update(state='pending-publication', reason='Named by the unresolved publication intent')
            elif record is None:
                item.update(state='unrecorded', reason='Carries receipt.json; see receipt reconciliation'
                            if 'receipt.json' in files else 'No completion proof exists; never pruned')
            elif record.get('status') == 'deleting':
                resumable.append(name)
                item.update(state='deleting', reason='Interrupted deletion of a verified bundle')
            elif record.get('status') != 'completed':
                item.update(state='unknown-contents', reason='Directory exists for a finished record')
            else:
                try:
                    candidates.append((_published(record['published_at']), name))
                except (KeyError, TypeError, ValueError):
                    item.update(state='verification-failed', reason='Malformed publication record')
        candidates.sort()
        for _, name in candidates[-keep:]:
            items[name].update(state='retained-window', reason=f'Among the {keep} most recent completed bundles')
        changed = False
        work = [(name, True) for name in resumable] + [(name, False) for _, name in candidates[:-keep]]
        for name, resumed in work:
            item, record = items[name], records[name]
            pin = next((label for label, sha in pins.items() if sha and sha == record.get('dataset_sha')), None)
            if pin and not resumed:
                item.update(state=pin, reason='Referenced by current worker state')
                continue
            if not resumed and record.get('checkpoint_before') == checkpoint_id:
                item.update(state='awaiting-checkpoint', reason='No private checkpoint since completion was recorded')
                continue
            if reasons:
                if not resumed:
                    item.update(state='blocked', reason='Volume-wide blocker; not verified')
                continue
            if resumed and not apply:
                continue
            fd = None
            try:
                try:
                    fd = _open_bundle(root_fd, name)
                except FileNotFoundError:
                    if not resumed:
                        raise
                if apply and (len(report['deleted']) >= max_delete
                              or (deadline is not None and clock() >= deadline)):
                    item.update(state='deferred', reason='Per-run deletion or time bound reached')
                    continue
                files = (_contents(fd) if fd is not None else {}) if resumed else _verify(fd, record, public)
                if not resumed:
                    item.update(state='eligible', reason='Verified locally and at the immutable public revision')
                    report['bytes_reclaimable'] += item['bytes']
                    if not apply:
                        item['action'] = 'would-delete'
                        continue
                if log is None:
                    try:
                        log = os.open(RECEIPTS, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                                      0o600, dir_fd=root_fd)
                        if not stat.S_ISREG(os.fstat(log).st_mode):
                            raise OSError('Receipt log is not a regular file')
                    except OSError:
                        reasons.append('Retention receipt log unavailable')
                        item.update(state='deferred', reason='Receipt log unavailable')
                        continue
                if not resumed:
                    # Durable intent before the first unlink; a killed run resumes from it.
                    record.update(status='deleting', files=files, bytes=sum(files.values()), deleting_at=_now())
                    state.set(db, STATE_KEY, records)
                    changed = True
                for entry in sorted(files):
                    os.unlink(entry, dir_fd=fd)
                if fd is not None:
                    os.rmdir(name, dir_fd=root_fd)
                    os.fsync(root_fd)
                receipt = {'name': name, 'dataset': DATASET, 'dataset_sha': record['dataset_sha'],
                    'files': {entry: {key: record['artifacts'][f'data/{entry}'][key] for key in ('bytes', 'sha256')}
                              for entry in ARTIFACTS},
                    'bytes_freed': record['bytes'], 'deleted_at': _now(), 'resumed': resumed, 'keep': keep,
                    'source': record.get('source'), 'published_at': record.get('published_at'),
                    'private_checkpoint': marker.get('checkpoint'), 'checkpoint_id': checkpoint_id}
                records[name] = {'status': 'deleted', 'source': record.get('source'),
                    'dataset_sha': record['dataset_sha'], 'published_at': record.get('published_at'), 'receipt': receipt}
                state.set(db, STATE_KEY, _bounded(records))
                changed = True
                try:
                    os.write(log, (json.dumps(receipt, sort_keys=True) + '\n').encode())
                    os.fsync(log)
                except OSError:
                    # The receipt is already durable in worker state; stop further deletions.
                    reasons.append('Retention receipt log write failed')
                report['deleted'].append(name)
                report['bytes_freed'] += receipt['bytes_freed']
                item.update(state='deleted', action='deleted', reason='Verified and removed')
            except UnknownContents as exc:
                item.update(state='unknown-contents', reason=failure_reason(exc))
            except Exception as exc:
                # One unverifiable bundle never stops proven ones or raises into the job.
                item.update(state='deleting' if records[name].get('status') == 'deleting' else 'verification-failed',
                            reason=failure_reason(exc))
            finally:
                if fd is not None:
                    os.close(fd)
        if apply and not reasons and report['missing']:
            for name in report['missing']:
                records[name] = {key: records[name].get(key) for key in ('source', 'dataset_sha', 'published_at')} | {
                    'status': 'missing', 'noticed_at': _now()}
            state.set(db, STATE_KEY, _bounded(records))
            changed = True
        report['changed'] = changed
        report['bundles'] = [items[name] for name in sorted(items)]
    finally:
        if log is not None:
            os.close(log)
        os.close(root_fd)
    if reasons:
        report['phase'] = 'blocked'
    return report


def reconcile_receipts(state, db, public, *, apply=False):
    """Adopt older bundles whose own receipt.json still proves an exact public revision."""
    _require_lock(state)
    records = state.get(db, STATE_KEY, {})
    if not isinstance(records, dict):
        raise ValueError('Malformed retention records')
    pending = _pending_name(state, db)
    report = {'adopted': [], 'adoptable': [], 'unproven': []}
    root_fd = os.open(state.directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in sorted(os.listdir(root_fd)):
            if not BUNDLE.fullmatch(name) or name in records or name == pending:
                continue
            problem, reason, size, files = _inspect(root_fd, name)
            if problem:
                report['unproven'].append({'name': name, 'bytes': size, 'reason': reason})
                continue
            fd = None
            try:
                if 'receipt.json' not in files:
                    raise ValueError('No receipt; completion cannot be proven')
                fd = _open_bundle(root_fd, name)
                receipt = json.loads(_read(fd, 'receipt.json', keep=True)[1])
                if not isinstance(receipt, dict) or receipt.get('dataset') != DATASET:
                    raise ValueError('Receipt is not for the public dataset')
                record = _record(receipt, source='receipt', checkpoint_id=state.get(db, 'checkpoint_id'))
                _verify(fd, record, public)
            except Exception as exc:
                reason = failure_reason(exc)
                report['unproven'].append({'name': name, 'bytes': size, 'reason':
                    reason if ':' in reason else f'{reason}: receipt or remote proof unavailable'})
                continue
            finally:
                if fd is not None:
                    os.close(fd)
            report['adoptable'].append(name)
            if apply:
                records[name] = record
                report['adopted'].append(name)
        if report['adopted']:
            state.set(db, STATE_KEY, records)
    finally:
        os.close(root_fd)
    return report


def run_retention(state, public, *, keep=3, apply=False, reconcile=False, max_delete=10):
    """Operator command: takes the worker lock itself, so it never runs beside the cron job."""
    with state.lock():
        db = state.open_normal()
        try:
            result = None
            if reconcile:
                result = reconcile_receipts(state, db, public, apply=apply and not blockers(state, db)[0])
                if result['adopted']:
                    state.checkpoint(db)
            report = retain(state, db, public, keep=keep, apply=apply, max_delete=max_delete)
            if result is not None:
                report['reconcile'] = result
            if report['changed'] and not report['blockers']:
                # Carry deletion receipts to the private checkpoint.
                state.checkpoint(db)
            return report
        finally:
            db.close()
