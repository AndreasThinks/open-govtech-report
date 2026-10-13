"""Create a fresh bundle; refuses to reuse a previous output directory."""
import argparse
import subprocess
from govtech_scraper.publication import prepare_publication

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='govtech.db')
    parser.add_argument('--output', default='publication')
    args = parser.parse_args()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    prepare_publication(args.db, args.output, commit)
