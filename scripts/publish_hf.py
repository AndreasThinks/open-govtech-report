"""Publish dataset to HuggingFace Hub."""

import os
import sys
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main():
    token = os.getenv("HF_TOKEN")
    if not token:
        print("HF_TOKEN not set, skipping HuggingFace publish")
        return

    try:
        from huggingface_hub import HfApi, CommitOperationAdd
    except ImportError:
        print("huggingface_hub not installed, skipping")
        return

    repo_id = "AndreasThinks/government-github-repos"
    api = HfApi(token=token)

    # Ensure the dataset repo exists
    try:
        api.create_repo(repo_id, repo_type="dataset", exist_ok=True)
    except Exception as e:
        print(f"Warning creating repo: {e}")

    # Collect files to upload
    operations = []
    files_to_upload = {
        "government_repos_latest.csv": "data/government_repos_latest.csv",
        "government_repos_latest.parquet": "data/government_repos_latest.parquet",
        "govtech.db": "data/govtech.db",
    }

    for local_path, hf_path in files_to_upload.items():
        if Path(local_path).exists():
            operations.append(
                CommitOperationAdd(
                    path_in_repo=hf_path,
                    path_or_fileobj=local_path,
                )
            )
            size_mb = Path(local_path).stat().st_size / (1024 * 1024)
            print(f"  {local_path} -> {hf_path} ({size_mb:.1f} MB)")
        else:
            print(f"  {local_path} not found, skipping")

    if not operations:
        print("No files to upload")
        return

    # Generate dataset card
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Get stats from DB
    stats_text = ""
    if Path("govtech.db").exists():
        conn = sqlite3.connect("govtech.db")
        repo_count = conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
        acct_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        country_count = conn.execute(
            "SELECT COUNT(DISTINCT country) FROM accounts"
        ).fetchone()[0]
        tag_count = conn.execute(
            "SELECT COUNT(*) FROM tags WHERE merged_into IS NULL"
        ).fetchone()[0]
        tagged_count = conn.execute(
            "SELECT COUNT(DISTINCT html_url) FROM repository_tags"
        ).fetchone()[0]
        conn.close()
        stats_text = f"""## Current Stats

| Metric | Value |
|--------|-------|
| Repositories | {repo_count:,} |
| Government accounts | {acct_count:,} |
| Countries | {country_count} |
| Taxonomy tags | {tag_count:,} |
| Tagged repositories | {tagged_count:,} |
"""

    readme_content = f"""---
license: agpl-3.0
task_categories:
  - text-classification
language:
  - en
tags:
  - government
  - open-source
  - github
  - public-sector
pretty_name: Government GitHub Repositories
size_categories:
  - 10K<n<100K
---

# Government GitHub Repositories

A comprehensive, regularly updated dataset of public GitHub repositories belonging to government organizations worldwide.

Sourced from the [government.github.com](https://government.github.com) registry. Updated weekly via automated pipeline.

{stats_text}

## Files

- `data/government_repos_latest.csv` — Latest repository metadata (CSV)
- `data/government_repos_latest.parquet` — Same data in Parquet format
- `data/govtech.db` — Full SQLite database with historical snapshots and tags

## Schema

Each row represents a government GitHub repository with fields:

| Field | Description |
|-------|-------------|
| owner | GitHub org/user name |
| name | Repository name |
| html_url | Repository URL |
| country | Country of the government org |
| description | Repository description |
| language | Primary programming language |
| stars | Star count |
| forks | Fork count |
| license | SPDX license identifier |
| topics | GitHub topics (JSON array) |
| archived | Whether the repo is archived |
| created_at | Creation timestamp |
| updated_at | Last update timestamp |

## Source

Scraped using [open-govtech-report](https://github.com/AndreasThinks/open-govtech-report).

Last updated: {now}
"""

    operations.append(
        CommitOperationAdd(
            path_in_repo="README.md",
            path_or_fileobj=readme_content.encode(),
        )
    )

    # Commit
    print(f"\nPushing {len(operations)} files to {repo_id}...")
    api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        operations=operations,
        commit_message=f"Update dataset ({now})",
    )
    print(f"Published to https://huggingface.co/datasets/{repo_id}")


if __name__ == "__main__":
    main()
