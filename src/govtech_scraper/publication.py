"""Fail-closed publication and restore. No network writes in this module."""
from contextlib import closing
import re
import csv
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .export import (export_to_csv, export_to_parquet, export_tag_groups_csv,
                     export_tag_group_members_csv)

FILES = ('govtech.db', 'government_repos_latest.csv', 'government_repos_latest.parquet',
         'tag_groups.csv', 'tag_group_members.csv')
REQUIRED = {
    'repositories': {'html_url', 'owner', 'country', 'last_scraped'},
    'repository_snapshots': {'html_url', 'scrape_timestamp'},
    'accounts': {'username', 'country'},
    'tags': {'tag', 'usage_count'},
    'repository_tags': {'html_url', 'tag', 'source'},
    'tag_groups': {'id', 'name'},
    'tag_group_members': {'tag', 'group_id'},
    'http_cache': {'url', 'etag'},
}


def connect_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def validate_database(path):
    with closing(connect_readonly(path)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        for table, columns in REQUIRED.items():
            actual = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
            if not columns <= actual:
                raise ValueError(f'Missing required schema: {table}')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('SQLite foreign key check failed')
        return {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in REQUIRED}


def restore_database(url, target, token=None, attempts=3):
    """Stream to a sibling temporary file; retain previous DB on every failure.

    Target must be offline (no writers/readers with WAL sidecars) during restore.
    """
    target = Path(target)
    if any(Path(str(target) + suffix).exists() for suffix in ('-wal', '-shm')):
        raise RuntimeError('Close database connections before restoring')
    for attempt in range(attempts):
        temp = None
        try:
            headers = {'Authorization': f'Bearer {token}'} if token else {}
            request = urllib.request.Request(url, headers=headers)
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.partial', delete=False) as out:
                temp = Path(out.name)
                with urllib.request.urlopen(request, timeout=60) as response:
                    # Bound total transfer time as well as individual reads.
                    deadline = time.monotonic() + 600
                    while True:
                        if time.monotonic() > deadline:
                            raise TimeoutError('Download deadline exceeded')
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
            validate_database(temp)
            os.replace(temp, target)
            return
        except Exception:
            if attempt + 1 == attempts:
                raise RuntimeError('Database restore failed; previous database retained') from None
            time.sleep(min(2 ** attempt, 8))
        finally:
            if temp:
                temp.unlink(missing_ok=True)


def _sha(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def database_metadata(path):
    counts = validate_database(path)
    with closing(connect_readonly(path)) as conn:
        conn.row_factory = sqlite3.Row
        latest = conn.execute('SELECT MAX(last_scraped) FROM repositories').fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        coverage = {'complete': False, 'reason': 'No recorded scrape coverage'}
        if 'scrape_runs' in tables:
            row = conn.execute('SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 1').fetchone()
            if row:
                coverage = dict(row)
                coverage['complete'] = bool(coverage['complete'])
                coverage['outcomes'] = dict(conn.execute(
                    'SELECT status, COUNT(*) FROM account_fetches WHERE run_id=? GROUP BY status', (row['id'],)))
                outcomes = coverage['outcomes']
                coverage['complete'] = bool(coverage['complete'] and row['finished_at']
                    and row['expected'] > 0 and row['expected'] == row['attempted']
                    and sum(outcomes.values()) == row['attempted']
                    and not outcomes.get('failed')
                    and set(outcomes) <= {'succeeded', 'unchanged', 'not-found'})
        counts['countries'] = conn.execute('SELECT COUNT(DISTINCT country) FROM accounts').fetchone()[0]
        counts['tagged_repositories'] = conn.execute('SELECT COUNT(DISTINCT html_url) FROM repository_tags WHERE source="llm"').fetchone()[0]
        counts['empty_untagged_repositories'] = conn.execute("""SELECT COUNT(*) FROM repositories r
            WHERE COALESCE(TRIM(description),'')='' AND language IS NULL AND stars=0
            AND NOT EXISTS (SELECT 1 FROM repository_tags rt WHERE rt.html_url=r.html_url AND source='llm')""").fetchone()[0]
        return counts, latest, coverage


def prepare_publication(db_path, directory, source_commit):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    snapshot = directory / 'govtech.db'
    validate_database(db_path)
    with closing(connect_readonly(db_path)) as source, closing(sqlite3.connect(snapshot)) as dest:
        source.backup(dest)
    counts, scraped, coverage = database_metadata(snapshot)
    export_to_csv(str(snapshot), str(directory / FILES[1]))
    export_to_parquet(str(snapshot), str(directory / FILES[2]))
    export_tag_groups_csv(str(snapshot), str(directory / FILES[3]))
    export_tag_group_members_csv(str(snapshot), str(directory / FILES[4]))
    manifest = {
        'schema_version': 1, 'source_commit': source_commit,
        'publication_timestamp': datetime.now(timezone.utc).isoformat(),
        'scrape_timestamp': scraped, 'coverage': coverage, 'counts': counts,
        'export_verified': True,
        'files': {name: {'sha256': _sha(directory / name), 'bytes': (directory / name).stat().st_size}
                  for name in FILES},
    }
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    validate_bundle(directory)
    return manifest


def validate_bundle(directory):
    import pyarrow.parquet as pq
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if not re.fullmatch(r'[0-9a-f]{40,64}', manifest.get('source_commit', '')):
        raise ValueError('Invalid source commit')
    counts, scraped, coverage = database_metadata(directory / 'govtech.db')
    if (manifest['counts'], manifest['scrape_timestamp'], manifest['coverage']) != (counts, scraped, coverage):
        raise ValueError('Manifest does not describe database')
    for name in FILES:
        path = directory / name
        if manifest['files'][name] != {'sha256': _sha(path), 'bytes': path.stat().st_size}:
            raise ValueError(f'Artifact checksum mismatch: {name}')
    with closing(connect_readonly(directory / 'govtech.db')) as conn:
        # Compare every value as well as URL uniqueness and counts.
        cursor = conn.execute('SELECT * FROM repositories ORDER BY stars DESC')
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
        expected = [[str(v) if v is not None else '' for v in row] for row in rows]
        with (directory / FILES[1]).open(newline='', encoding='utf-8') as stream:
            csv_rows = list(csv.reader(stream))
        if csv_rows != [columns] + expected:
            raise ValueError('CSV differs from snapshot')
        table = pq.read_table(directory / FILES[2])
        if table.column_names != columns or table.to_pylist() != [dict(zip(columns, row)) for row in rows]:
            raise ValueError('Parquet differs from snapshot')
        urls = [row[columns.index('html_url')] for row in rows]
        if len(set(urls)) != len(urls) or any(not u for u in urls):
            raise ValueError('Invalid repository URLs')
        for table_name, file_name, order in [('tag_groups', FILES[3], 'name'), ('tag_group_members', FILES[4], 'group_id, tag')]:
            cursor = conn.execute(f'SELECT * FROM {table_name} ORDER BY {order}')
            expected = [[c[0] for c in cursor.description]] + [[str(v) if v is not None else '' for v in r] for r in cursor]
            with (directory / file_name).open(newline='', encoding='utf-8') as stream:
                if list(csv.reader(stream)) != expected:
                    raise ValueError(f'{file_name} differs from snapshot')
        if conn.execute('SELECT 1 FROM repository_tags rt LEFT JOIN tags t ON rt.tag=t.tag LEFT JOIN repositories r ON rt.html_url=r.html_url WHERE t.tag IS NULL OR r.html_url IS NULL LIMIT 1').fetchone():
            raise ValueError('Orphan repository tag links; run offline migration')
    return manifest


def check_freshness(manifest, max_age_days=14, now=None):
    now = now or datetime.now(timezone.utc)
    if not manifest.get('export_verified') or not manifest.get('coverage', {}).get('complete'):
        raise ValueError('Unverified or incomplete publication')
    timestamps = {key: manifest.get(key) for key in ('publication_timestamp', 'scrape_timestamp')}
    timestamps['coverage_finished_at'] = manifest['coverage'].get('finished_at')
    for key, value in timestamps.items():
        try:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            age = now - date
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ValueError(f'Missing or invalid {key}') from None
        if age > timedelta(days=max_age_days) or age < -timedelta(minutes=10):
            raise ValueError(f'Stale or future {key}')
