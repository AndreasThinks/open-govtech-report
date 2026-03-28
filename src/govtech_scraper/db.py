"""SQLite storage - single source of truth.

Stores repository metadata, historical snapshots, account mappings,
and HTTP cache headers (ETag/Last-Modified).
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Repository, GovernmentAccount

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "govtech.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    username TEXT PRIMARY KEY,
    country TEXT NOT NULL,
    account_type TEXT DEFAULT 'org',
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repositories (
    html_url TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    description TEXT,
    language TEXT,
    stars INTEGER DEFAULT 0,
    forks INTEGER DEFAULT 0,
    watchers INTEGER DEFAULT 0,
    open_issues INTEGER DEFAULT 0,
    size_kb INTEGER DEFAULT 0,
    license TEXT,
    topics TEXT DEFAULT '[]',
    default_branch TEXT DEFAULT 'main',
    fork INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    pushed_at TEXT,
    commit_count INTEGER DEFAULT 0,
    fork_source TEXT,
    last_scraped TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repository_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    html_url TEXT NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    stars INTEGER DEFAULT 0,
    forks INTEGER DEFAULT 0,
    watchers INTEGER DEFAULT 0,
    open_issues INTEGER DEFAULT 0,
    commit_count INTEGER DEFAULT 0,
    scrape_timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS http_cache (
    url TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    cached_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repos_country ON repositories(country);
CREATE INDEX IF NOT EXISTS idx_repos_language ON repositories(language);
CREATE INDEX IF NOT EXISTS idx_repos_stars ON repositories(stars);
CREATE INDEX IF NOT EXISTS idx_snapshots_url ON repository_snapshots(html_url);
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON repository_snapshots(scrape_timestamp);

CREATE TABLE IF NOT EXISTS tags (
    tag TEXT PRIMARY KEY,
    embedding BLOB,
    usage_count INTEGER DEFAULT 0,
    first_seen TEXT,
    merged_into TEXT
);

CREATE TABLE IF NOT EXISTS repository_tags (
    html_url TEXT NOT NULL,
    tag TEXT NOT NULL,
    confidence REAL DEFAULT 0.8,
    source TEXT DEFAULT 'llm',
    PRIMARY KEY (html_url, tag)
);

CREATE INDEX IF NOT EXISTS idx_repo_tags_url ON repository_tags(html_url);
CREATE INDEX IF NOT EXISTS idx_repo_tags_tag ON repository_tags(tag);
CREATE INDEX IF NOT EXISTS idx_tags_usage ON tags(usage_count);
"""


class Database:
    """SQLite database manager."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def save_accounts(self, accounts: list[GovernmentAccount]) -> None:
        """Upsert government accounts."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            """INSERT INTO accounts (username, country, account_type, last_seen)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(username) DO UPDATE SET
                   country = excluded.country,
                   last_seen = excluded.last_seen""",
            [(a.username, a.country, a.account_type, now) for a in accounts],
        )
        self.conn.commit()

    def save_repositories(self, repos: list[Repository]) -> None:
        """Upsert repositories and create snapshots."""
        now = datetime.now(timezone.utc).isoformat()
        for repo in repos:
            # Upsert into repositories
            self.conn.execute(
                """INSERT INTO repositories (
                       html_url, owner, name, country, description, language,
                       stars, forks, watchers, open_issues, size_kb, license,
                       topics, default_branch, fork, archived, created_at,
                       updated_at, pushed_at, commit_count, fork_source, last_scraped
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(html_url) DO UPDATE SET
                       owner = excluded.owner, name = excluded.name,
                       country = excluded.country, description = excluded.description,
                       language = excluded.language, stars = excluded.stars,
                       forks = excluded.forks, watchers = excluded.watchers,
                       open_issues = excluded.open_issues, size_kb = excluded.size_kb,
                       license = excluded.license, topics = excluded.topics,
                       default_branch = excluded.default_branch, fork = excluded.fork,
                       archived = excluded.archived, created_at = excluded.created_at,
                       updated_at = excluded.updated_at, pushed_at = excluded.pushed_at,
                       commit_count = excluded.commit_count, fork_source = excluded.fork_source,
                       last_scraped = excluded.last_scraped""",
                (
                    repo.html_url, repo.owner, repo.name, repo.country,
                    repo.description, repo.language, repo.stars, repo.forks,
                    repo.watchers, repo.open_issues, repo.size_kb, repo.license,
                    json.dumps(repo.topics), repo.default_branch,
                    int(repo.fork), int(repo.archived), repo.created_at,
                    repo.updated_at, repo.pushed_at, repo.commit_count,
                    repo.fork_source, now,
                ),
            )
            # Insert snapshot
            self.conn.execute(
                """INSERT INTO repository_snapshots (
                       html_url, owner, name, country, stars, forks,
                       watchers, open_issues, commit_count, scrape_timestamp
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    repo.html_url, repo.owner, repo.name, repo.country,
                    repo.stars, repo.forks, repo.watchers, repo.open_issues,
                    repo.commit_count, now,
                ),
            )
        self.conn.commit()
        logger.info(f"Saved {len(repos)} repositories")

    def get_cache(self, url: str) -> Optional[dict[str, str]]:
        """Get cached ETag/Last-Modified for a URL."""
        row = self.conn.execute(
            "SELECT etag, last_modified FROM http_cache WHERE url = ?", (url,)
        ).fetchone()
        if row:
            return {"etag": row["etag"], "last_modified": row["last_modified"]}
        return None

    def set_cache(self, url: str, etag: Optional[str], last_modified: Optional[str]) -> None:
        """Store ETag/Last-Modified for a URL."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO http_cache (url, etag, last_modified, cached_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   etag = excluded.etag,
                   last_modified = excluded.last_modified,
                   cached_at = excluded.cached_at""",
            (url, etag, last_modified, now),
        )
        self.conn.commit()

    def get_repo_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM repositories").fetchone()
        return row[0]

    # --- Tag-related methods ---

    def save_repo_tag(
        self, html_url: str, tag: str, confidence: float = 0.8, source: str = "llm"
    ) -> None:
        """Associate a tag with a repository."""
        self.conn.execute(
            """INSERT INTO repository_tags (html_url, tag, confidence, source)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(html_url, tag) DO UPDATE SET
                   confidence = excluded.confidence,
                   source = excluded.source""",
            (html_url, tag, confidence, source),
        )
        self.conn.commit()

    def get_all_repos(self) -> list[sqlite3.Row]:
        """Get all repositories."""
        return self.conn.execute(
            "SELECT * FROM repositories ORDER BY stars DESC"
        ).fetchall()

    def get_untagged_repos(self) -> list[sqlite3.Row]:
        """Get repositories that haven't been tagged yet."""
        return self.conn.execute(
            """SELECT r.* FROM repositories r
               WHERE r.html_url NOT IN (
                   SELECT DISTINCT html_url FROM repository_tags WHERE source = 'llm'
               )
               ORDER BY r.stars DESC"""
        ).fetchall()

    def get_readme(self, html_url: str) -> Optional[str]:
        """Get README content for a repository, if stored.
        
        Note: README storage will be added when README fetching is implemented.
        For now, returns None.
        """
        # Future: SELECT readme_content FROM readmes WHERE html_url = ?
        return None

    def get_tag_stats(self) -> dict:
        """Get statistics about tags."""
        total_tags = self.conn.execute(
            "SELECT COUNT(*) FROM tags WHERE merged_into IS NULL"
        ).fetchone()[0]
        total_tagged_repos = self.conn.execute(
            "SELECT COUNT(DISTINCT html_url) FROM repository_tags"
        ).fetchone()[0]
        total_repo_tags = self.conn.execute(
            "SELECT COUNT(*) FROM repository_tags"
        ).fetchone()[0]
        top_tags = self.conn.execute(
            """SELECT tag, usage_count FROM tags
               WHERE merged_into IS NULL
               ORDER BY usage_count DESC LIMIT 20"""
        ).fetchall()
        return {
            "total_tags": total_tags,
            "total_tagged_repos": total_tagged_repos,
            "total_repo_tags": total_repo_tags,
            "avg_tags_per_repo": (
                round(total_repo_tags / total_tagged_repos, 1) if total_tagged_repos > 0 else 0
            ),
            "top_tags": [(row["tag"], row["usage_count"]) for row in top_tags],
        }

    def close(self) -> None:
        self.conn.close()
