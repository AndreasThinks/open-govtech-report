"""Roll out a verified receipt; no code, domains, or other variables are changed."""
import argparse
import json
import os
from pathlib import Path
from huggingface_hub import HfApi
from govtech_scraper.operations import update_index, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', default='publication/receipt.json')
    parser.add_argument('--attempts', type=int, default=30)
    parser.add_argument('--diagnose-native-fallback', action='store_true')
    args = parser.parse_args()
    result_path = Path(args.receipt).with_name('index-verification.json')
    result_path.unlink(missing_ok=True)
    result = update_index(json.loads(Path(args.receipt).read_text()),
                          HfApi(token=os.environ['HF_TOKEN']), attempts=args.attempts,
                          allow_native_fallback=args.diagnose_native_fallback)
    write_json(result_path, result)
    print('Index verified at canonical endpoint for dataset ' + result['dataset_sha'])


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(f'Index update failed ({type(exc).__name__}); inspect persisted restart intent before retry') from None
