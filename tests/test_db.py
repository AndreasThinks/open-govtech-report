"""Tests for database operations."""

import json
import pytest

from govtech_scraper.db import Database
from govtech_scraper.models import Repository, GovernmentAccount


def test_schema_creation(tmp_db):
    tables = [r[0] for r in tmp_db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    assert "accounts" in tables
    assert "repositories" in tables
    assert "repository_snapshots" in tables
    assert "http_cache" in tables
    assert "tags" in tables
    assert "repository_tags" in tables


def test_save_and_count_repos(tmp_db, sample_repo):
    assert tmp_db.get_repo_count() == 0
    tmp_db.save_repositories([sample_repo])
    assert tmp_db.get_repo_count() == 1


def test_repo_upsert(tmp_db, sample_repo):
    tmp_db.save_repositories([sample_repo])
    # Update stars and save again
    sample_repo.stars = 200
    tmp_db.save_repositories([sample_repo])
    assert tmp_db.get_repo_count() == 1
    row = tmp_db.conn.execute(
        "SELECT stars FROM repositories WHERE html_url = ?",
        (sample_repo.html_url,)
    ).fetchone()
    assert row["stars"] == 200


def test_snapshots_created(tmp_db, sample_repo):
    tmp_db.save_repositories([sample_repo])
    tmp_db.save_repositories([sample_repo])  # save again
    snapshots = tmp_db.conn.execute(
        "SELECT COUNT(*) FROM repository_snapshots WHERE html_url = ?",
        (sample_repo.html_url,)
    ).fetchone()[0]
    assert snapshots == 2  # two snapshots from two saves


def test_save_accounts(tmp_db, sample_account):
    tmp_db.save_accounts([sample_account])
    count = tmp_db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    assert count == 1


def test_http_cache(tmp_db):
    url = "https://api.github.com/test"
    assert tmp_db.get_cache(url) is None
    tmp_db.set_cache(url, etag='"abc123"', last_modified="Wed, 01 Jan 2025")
    cached = tmp_db.get_cache(url)
    assert cached["etag"] == '"abc123"'
    assert cached["last_modified"] == "Wed, 01 Jan 2025"


def test_save_repo_tag(tmp_db, sample_repo):
    tmp_db.save_repositories([sample_repo])
    tmp_db.save_repo_tag(sample_repo.html_url, "open-data", 0.95, "llm")
    tmp_db.save_repo_tag(sample_repo.html_url, "data-portal", 0.85, "llm")
    rows = tmp_db.conn.execute(
        "SELECT * FROM repository_tags WHERE html_url = ?",
        (sample_repo.html_url,)
    ).fetchall()
    assert len(rows) == 2


def test_repo_tag_upsert(tmp_db, sample_repo):
    tmp_db.save_repositories([sample_repo])
    tmp_db.save_repo_tag(sample_repo.html_url, "open-data", 0.5, "llm")
    tmp_db.save_repo_tag(sample_repo.html_url, "open-data", 0.95, "llm")
    rows = tmp_db.conn.execute(
        "SELECT confidence FROM repository_tags WHERE html_url = ? AND tag = ?",
        (sample_repo.html_url, "open-data")
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.95


def test_get_untagged_repos(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    untagged = tmp_db.get_untagged_repos()
    assert len(untagged) == 3
    # Tag one
    tmp_db.save_repo_tag(sample_repos[0].html_url, "notifications", 0.9, "llm")
    untagged = tmp_db.get_untagged_repos()
    assert len(untagged) == 2


def test_get_all_repos(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    all_repos = tmp_db.get_all_repos()
    assert len(all_repos) == 3


def test_tag_stats_empty(tmp_db):
    stats = tmp_db.get_tag_stats()
    assert stats["total_tags"] == 0
    assert stats["total_tagged_repos"] == 0
    assert stats["avg_tags_per_repo"] == 0


def test_tag_stats_with_data(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    # Add some tags to the tags table
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    tmp_db.conn.execute(
        "INSERT INTO tags (tag, usage_count, first_seen) VALUES (?, ?, ?)",
        ("open-data", 3, now)
    )
    tmp_db.conn.execute(
        "INSERT INTO tags (tag, usage_count, first_seen) VALUES (?, ?, ?)",
        ("api", 2, now)
    )
    tmp_db.conn.commit()
    tmp_db.save_repo_tag(sample_repos[0].html_url, "open-data", 0.9, "llm")
    tmp_db.save_repo_tag(sample_repos[0].html_url, "api", 0.8, "llm")
    tmp_db.save_repo_tag(sample_repos[1].html_url, "api", 0.85, "llm")

    stats = tmp_db.get_tag_stats()
    assert stats["total_tags"] == 2
    assert stats["total_tagged_repos"] == 2
    assert stats["total_repo_tags"] == 3
