"""Publish a verified bundle, recording the exact remote revision and readback."""
import json
import os
from pathlib import Path
from govtech_scraper.publication import validate_bundle, check_freshness
from govtech_scraper.operations import publish_bundle


def main(directory='publication', baseline=None):
    if not os.getenv('HF_TOKEN'):
        raise RuntimeError('HF_TOKEN is required for publication')
    directory = Path(directory)
    (directory / 'receipt.json').unlink(missing_ok=True)
    # Validate before constructing any authenticated client.
    check_freshness(validate_bundle(directory))
    baseline = Path(baseline) if baseline else directory / 'baseline.json'
    parent = json.loads(baseline.read_text())['dataset_sha']
    token = os.environ['HF_TOKEN']
    from huggingface_hub import HfApi
    receipt = publish_bundle(directory, HfApi(token=token), parent_commit=parent)
    print('Verified dataset commit: ' + receipt['dataset_sha'])
    return receipt


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default='publication')
    parser.add_argument('--baseline', default='baseline.json')
    args = parser.parse_args()
    try:
        main(args.directory, args.baseline)
    except Exception as exc:
        raise SystemExit(f'Publication failed ({type(exc).__name__}); no success reported') from None
