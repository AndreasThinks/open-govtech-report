"""Read-only independent monitor. Run externally; nonzero means stale/inconsistent."""
import argparse
import json
import hashlib
import time
import urllib.request
from datetime import datetime, timezone
from govtech_scraper.publication import FILES, check_freshness


def read_json(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.loads(response.read(2 * 1024 * 1024))
        except Exception:
            if attempt == 2:
                raise RuntimeError('Remote metadata request failed') from None
            import time
            time.sleep(2 ** attempt)


def remote_digest(url, expected_size):
    # Non-LFS files lack a SHA-256 in Hub metadata; stream and compare their bytes.
    if expected_size > 100 * 1024 * 1024:
        raise ValueError('Large artifact lacks LFS hash metadata')
    digest, size = hashlib.sha256(), 0
    deadline = time.monotonic() + 120
    with urllib.request.urlopen(url, timeout=30) as response:
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('Artifact verification deadline exceeded')
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > expected_size:
                raise ValueError('Artifact size exceeds manifest')
            digest.update(chunk)
    if size != expected_size:
        raise ValueError('Artifact truncated')
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', default='AndreasThinks/government-github-repos')
    parser.add_argument('--max-age-days', type=int, default=14)
    args = parser.parse_args(argv)
    metadata = read_json(f'https://huggingface.co/api/datasets/{args.repo}?blobs=true')
    revision = metadata['sha']
    manifest = read_json(f'https://huggingface.co/datasets/{args.repo}/resolve/{revision}/data/manifest.json')
    check_freshness(manifest, args.max_age_days)
    files = {item['rfilename']: item for item in metadata.get('siblings', [])}
    for name in FILES:
        item = files.get(f'data/{name}')
        expected = manifest.get('files', {}).get(name)
        if not item or not expected or item.get('size') != expected['bytes']:
            raise ValueError(f'Missing or inconsistent remote artifact: {name}')
        lfs = item.get('lfs')
        actual_hash = lfs.get('sha256') if lfs else remote_digest(
            f'https://huggingface.co/datasets/{args.repo}/resolve/{revision}/data/{name}', expected['bytes'])
        if actual_hash != expected['sha256']:
            raise ValueError(f'Remote artifact hash differs: {name}')
    modified = datetime.fromisoformat(metadata['lastModified'].replace('Z', '+00:00'))
    published = datetime.fromisoformat(manifest['publication_timestamp'].replace('Z', '+00:00'))
    if modified < published or (modified - published).total_seconds() > 86400:
        raise ValueError('Remote revision and manifest publication dates disagree')
    print(f'Fresh verified publication at {revision}')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(f'Freshness check failed ({type(exc).__name__})') from None
