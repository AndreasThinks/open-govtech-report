"""Restore immutable history and retain its revision for optimistic publication."""
import os
import urllib.request  # kept visible for boundary mocking
from pathlib import Path
from govtech_scraper.publication import restore_database
from govtech_scraper.operations import DATASET, valid_sha, write_json


def main():
    from huggingface_hub import HfApi
    baseline = Path('baseline.json')
    baseline.unlink(missing_ok=True)
    token = os.getenv('HF_TOKEN')
    try:
        sha = valid_sha(HfApi(token=token).repo_info(repo_id=DATASET, repo_type='dataset').sha)
        restore_database(
            f'https://huggingface.co/datasets/{DATASET}/resolve/{sha}/data/govtech.db',
            'govtech.db', token=token)
        write_json(baseline, {'dataset': DATASET, 'dataset_sha': sha})
    except Exception:
        raise RuntimeError('Immutable database restore failed; no baseline receipt written') from None
    print('Validated database restored at ' + sha)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(f'Restore failed ({type(exc).__name__})') from None
