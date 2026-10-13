"""SQLite storage for decision-model results.

Kept in their own tables so the experiment never touches the LLM tags in `repository_tags`.
Rows are keyed by taxonomy version and model, so runs with different backends sit side by
side and can be compared.
"""

import json
import sqlite3
from datetime import UTC, datetime

from ..db import Database
from .scorer import DecisionResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_results (
    html_url TEXT NOT NULL,
    taxonomy_version INTEGER NOT NULL,
    model TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    domain TEXT,
    kind TEXT,
    substantive REAL,
    error TEXT,
    scored_at TEXT NOT NULL,
    PRIMARY KEY (html_url, taxonomy_version, model)
);

CREATE TABLE IF NOT EXISTS decision_probabilities (
    html_url TEXT NOT NULL,
    taxonomy_version INTEGER NOT NULL,
    model TEXT NOT NULL,
    facet TEXT NOT NULL,              -- 'domain', 'kind' or 'tag'
    label TEXT NOT NULL,
    probability REAL NOT NULL,
    PRIMARY KEY (html_url, taxonomy_version, model, facet, label)
);

CREATE INDEX IF NOT EXISTS idx_decision_prob_label
    ON decision_probabilities(facet, label, probability);
"""


class DecisionStore:
    def __init__(self, db: Database):
        self.db = db
        self.conn: sqlite3.Connection = db.conn
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def save(self, results: list[DecisionResult], taxonomy_version: int, model: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.conn:
            for r in results:
                key = (r.html_url, taxonomy_version, model)
                self.conn.execute(
                    """INSERT OR REPLACE INTO decision_results
                       (html_url, taxonomy_version, model, input_hash, domain, kind, substantive, error, scored_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (*key, r.input_hash, r.domain, r.kind, r.substantive, r.error, now),
                )
                self.conn.execute(
                    "DELETE FROM decision_probabilities WHERE html_url=? AND taxonomy_version=? AND model=?",
                    key,
                )
                if r.error:
                    continue
                rows = (
                    [("domain", k, p) for k, p in r.domain_probabilities.items()]
                    + [("kind", k, p) for k, p in r.kind_probabilities.items()]
                    + [("tag", k, p) for k, p in r.tag_probabilities.items()]
                )
                self.conn.executemany(
                    """INSERT INTO decision_probabilities
                       (html_url, taxonomy_version, model, facet, label, probability)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [(*key, facet, label, p) for facet, label, p in rows],
                )

    def scored_hashes(self, taxonomy_version: int, model: str) -> dict[str, str]:
        """html_url -> input hash, for repos already scored without error."""
        rows = self.conn.execute(
            """SELECT html_url, input_hash FROM decision_results
               WHERE taxonomy_version=? AND model=? AND error IS NULL""",
            (taxonomy_version, model),
        )
        return {row[0]: row[1] for row in rows}

    def load(self, html_url: str, taxonomy_version: int, model: str) -> DecisionResult | None:
        row = self.conn.execute(
            """SELECT * FROM decision_results WHERE html_url=? AND taxonomy_version=? AND model=?""",
            (html_url, taxonomy_version, model),
        ).fetchone()
        if row is None:
            return None
        result = DecisionResult(
            html_url=row["html_url"],
            input_hash=row["input_hash"],
            domain=row["domain"],
            kind=row["kind"],
            substantive=row["substantive"],
            error=row["error"],
        )
        for facet, label, p in self.conn.execute(
            """SELECT facet, label, probability FROM decision_probabilities
               WHERE html_url=? AND taxonomy_version=? AND model=?""",
            (html_url, taxonomy_version, model),
        ):
            {
                "domain": result.domain_probabilities,
                "kind": result.kind_probabilities,
                "tag": result.tag_probabilities,
            }[facet][label] = p
        return result

    def models(self) -> list[tuple[int, str, int]]:
        """(taxonomy_version, model, scored repo count) for every run stored."""
        return [
            tuple(r)
            for r in self.conn.execute(
                """SELECT taxonomy_version, model, COUNT(*) FROM decision_results
               WHERE error IS NULL GROUP BY taxonomy_version, model ORDER BY 1 DESC, 3 DESC"""
            )
        ]


def row_to_repository(row: sqlite3.Row):
    """Build a Repository from a `repositories` row."""
    from ..models import Repository

    keys = row.keys()
    return Repository(
        owner=row["owner"],
        name=row["name"],
        html_url=row["html_url"],
        country=row["country"],
        description=row["description"],
        language=row["language"],
        stars=row["stars"] or 0,
        forks=row["forks"] or 0,
        watchers=row["watchers"] or 0,
        open_issues=row["open_issues"] or 0,
        size_kb=row["size_kb"] or 0,
        license=row["license"],
        topics=json.loads(row["topics"]) if row["topics"] else [],
        default_branch=row["default_branch"] or "main",
        fork=bool(row["fork"]),
        archived=bool(row["archived"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pushed_at=row["pushed_at"],
        commit_count=row["commit_count"],
        fork_source=row["fork_source"] if "fork_source" in keys else None,
    )
