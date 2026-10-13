"""Commit one previously prepared and validated bundle. No implicit exports."""
import os
from pathlib import Path
from govtech_scraper.publication import FILES, validate_bundle, check_freshness


def main(directory='publication'):
    token = os.getenv('HF_TOKEN')
    if not token:
        raise RuntimeError('HF_TOKEN is required for publication')
    from huggingface_hub import HfApi, CommitOperationAdd
    directory = Path(directory)
    manifest = validate_bundle(directory)
    check_freshness(manifest)
    operations = [CommitOperationAdd(path_in_repo=f'data/{name}', path_or_fileobj=str(directory / name))
                  for name in (*FILES, 'manifest.json')]
    card = f'''---
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
Batch tags use metadata only. commit_count NULL and fork_source NULL mean unknown.
'''
    operations.append(CommitOperationAdd(path_in_repo='README.md', path_or_fileobj=card.encode()))
    HfApi(token=token).create_commit(repo_id='AndreasThinks/government-github-repos', repo_type='dataset',
                                    operations=operations, commit_message=f"Verified dataset {manifest['publication_timestamp']}")
    print('Validated bundle published')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # HTTP exceptions can include request URLs or credentials. Do not print them.
        raise SystemExit(f'Publication failed ({type(exc).__name__}); no success reported') from None
