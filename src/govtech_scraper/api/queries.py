"""Read-only queries over the published SQLite database.

Shared by the REST API and the MCP tools so both answer the same question the same way.
Every function opens its own read-only connection, so the module is safe to call from
worker threads and can never change the data.
"""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

from ..decision.taxonomy import Taxonomy, load_taxonomy

MAX_LIMIT = 100
TAG_THRESHOLD = 0.5
SORTS = {
    "stars": "r.stars DESC",
    "recently_pushed": "r.pushed_at DESC",
    "newest": "r.created_at DESC",
    "name": "r.owner, r.name",
}


@dataclass
class Classification:
    model: str
    domain: str | None
    domain_name: str | None
    domain_probability: float | None
    kind: str | None
    tags: list[dict] = field(default_factory=list)


@dataclass
class RepoSummary:
    full_name: str
    html_url: str
    country: str
    description: str | None
    language: str | None
    stars: int
    forks: int
    license: str | None
    topics: list[str]
    fork: bool
    archived: bool
    created_at: str | None
    pushed_at: str | None
    domain: str | None = None
    kind: str | None = None


@dataclass
class RepoDetail(RepoSummary):
    open_issues: int = 0
    size_kb: int = 0
    default_branch: str | None = None
    fork_source: str | None = None
    llm_tags: list[str] = field(default_factory=list)
    classification: Classification | None = None


class Catalogue:
    """Query facade over one database file."""

    def __init__(self, db_path: str | None = None, taxonomy: Taxonomy | None = None):
        self.db_path = db_path or os.getenv("GOVTECH_DB", "govtech.db")
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        self.taxonomy = taxonomy or load_taxonomy()
        self._domain_names = {d.id: d.name for d in self.taxonomy.domains}
        self.decision_model = os.getenv("DECISION_MODEL") or self._default_decision_model()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _has_table(self, conn: sqlite3.Connection, table: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            is not None
        )

    def _default_decision_model(self) -> str | None:
        """The decision run covering the most repos for the current taxonomy, if any."""
        with self._connect() as conn:
            if not self._has_table(conn, "decision_results"):
                return None
            row = conn.execute(
                """SELECT model FROM decision_results
                   WHERE taxonomy_version=? AND error IS NULL
                   GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1""",
                (self.taxonomy.version,),
            ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ helpers

    def _decision_join(self) -> tuple[str, list]:
        if not self.decision_model:
            return "", []
        return (
            """LEFT JOIN decision_results d ON d.html_url = r.html_url
                 AND d.taxonomy_version = ? AND d.model = ? AND d.error IS NULL""",
            [self.taxonomy.version, self.decision_model],
        )

    @staticmethod
    def _summary(row: sqlite3.Row) -> RepoSummary:
        keys = row.keys()
        return RepoSummary(
            full_name=f"{row['owner']}/{row['name']}",
            html_url=row["html_url"],
            country=row["country"],
            description=row["description"],
            language=row["language"],
            stars=row["stars"] or 0,
            forks=row["forks"] or 0,
            license=row["license"],
            topics=json.loads(row["topics"]) if row["topics"] else [],
            fork=bool(row["fork"]),
            archived=bool(row["archived"]),
            created_at=row["created_at"],
            pushed_at=row["pushed_at"],
            domain=row["d_domain"] if "d_domain" in keys else None,
            kind=row["d_kind"] if "d_kind" in keys else None,
        )

    # ------------------------------------------------------------------ queries

    def search_repos(
        self,
        q: str | None = None,
        country: str | None = None,
        language: str | None = None,
        topic: str | None = None,
        domain: str | None = None,
        tag: str | None = None,
        kind: str | None = None,
        min_stars: int = 0,
        include_forks: bool = True,
        include_archived: bool = True,
        sort: str = "stars",
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Filter and page through repositories. Returns {"total", "items"}."""
        if sort not in SORTS:
            raise ValueError(f"sort must be one of {', '.join(SORTS)}")
        limit = max(1, min(limit, MAX_LIMIT))
        offset = max(0, offset)

        join, params = self._decision_join()
        select_extra = ", d.domain AS d_domain, d.kind AS d_kind" if join else ""
        where = ["COALESCE(r.current, 1) = 1", "r.stars >= ?"]
        params.append(min_stars)
        if q:
            where.append(
                "(r.name LIKE ? OR r.owner LIKE ? OR r.description LIKE ? OR r.topics LIKE ?)"
            )
            params += [f"%{q}%"] * 4
        if country:
            where.append("LOWER(r.country) = LOWER(?)")
            params.append(country)
        if language:
            where.append("LOWER(r.language) = LOWER(?)")
            params.append(language)
        if topic:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(r.topics) t WHERE LOWER(t.value) = LOWER(?))"
            )
            params.append(topic)
        if not include_forks:
            where.append("r.fork = 0")
        if not include_archived:
            where.append("r.archived = 0")
        if domain or tag or kind:
            if not join:
                raise ValueError("No classification data in this database yet")
            if domain:
                where.append("d.domain = ?")
                params.append(domain)
            if kind:
                where.append("d.kind = ?")
                params.append(kind)
            if tag:
                where.append(
                    """EXISTS (SELECT 1 FROM decision_probabilities p
                       WHERE p.html_url = r.html_url AND p.taxonomy_version = d.taxonomy_version
                         AND p.model = d.model AND p.facet = 'tag' AND p.label = ? AND p.probability >= ?)"""
                )
                params += [tag, TAG_THRESHOLD]

        sql_from = f"FROM repositories r {join} WHERE {' AND '.join(where)}"
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) {sql_from}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT r.*{select_extra} {sql_from} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [asdict(self._summary(row)) for row in rows],
        }

    def get_repo(self, owner: str, name: str) -> dict | None:
        """One repository with its tags and classification, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM repositories WHERE LOWER(owner)=LOWER(?) AND LOWER(name)=LOWER(?)",
                (owner, name),
            ).fetchone()
            if row is None:
                return None
            summary = self._summary(row)
            detail = RepoDetail(
                **asdict(summary),
                open_issues=row["open_issues"] or 0,
                size_kb=row["size_kb"] or 0,
                default_branch=row["default_branch"],
                fork_source=row["fork_source"] if "fork_source" in row.keys() else None,
            )
            if self._has_table(conn, "repository_tags"):
                detail.llm_tags = [
                    r[0]
                    for r in conn.execute(
                        "SELECT tag FROM repository_tags WHERE html_url=? AND source='llm' ORDER BY confidence DESC",
                        (row["html_url"],),
                    )
                ]
            if self.decision_model:
                detail.classification = self._classification(conn, row["html_url"])
                if detail.classification:
                    detail.domain = detail.classification.domain
                    detail.kind = detail.classification.kind
        return asdict(detail)

    def _classification(self, conn: sqlite3.Connection, html_url: str) -> Classification | None:
        result = conn.execute(
            """SELECT domain, kind FROM decision_results
               WHERE html_url=? AND taxonomy_version=? AND model=? AND error IS NULL""",
            (html_url, self.taxonomy.version, self.decision_model),
        ).fetchone()
        if result is None:
            return None
        probs = conn.execute(
            """SELECT facet, label, probability FROM decision_probabilities
               WHERE html_url=? AND taxonomy_version=? AND model=?
               ORDER BY probability DESC""",
            (html_url, self.taxonomy.version, self.decision_model),
        ).fetchall()
        domain_p = next(
            (p for f, label, p in probs if f == "domain" and label == result["domain"]), None
        )
        return Classification(
            model=self.decision_model,
            domain=result["domain"],
            domain_name=self._domain_names.get(result["domain"]),
            domain_probability=domain_p,
            kind=result["kind"],
            tags=[
                {"id": label, "probability": round(p, 3)}
                for f, label, p in probs
                if f == "tag" and p >= TAG_THRESHOLD
            ],
        )

    def list_countries(self) -> list[dict]:
        """Countries with repository counts."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT country, COUNT(*) AS repos, COUNT(DISTINCT owner) AS accounts,
                          SUM(stars) AS stars
                   FROM repositories WHERE COALESCE(current, 1) = 1
                   GROUP BY country ORDER BY repos DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def taxonomy_summary(self) -> dict:
        """The taxonomy, with how many repos each domain and tag covers in the current run."""
        domain_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        if self.decision_model:
            key = (self.taxonomy.version, self.decision_model)
            with self._connect() as conn:
                domain_counts = dict(
                    conn.execute(
                        """SELECT domain, COUNT(*) FROM decision_results
                       WHERE taxonomy_version=? AND model=? AND error IS NULL GROUP BY domain""",
                        key,
                    )
                )
                tag_counts = dict(
                    conn.execute(
                        """SELECT label, COUNT(*) FROM decision_probabilities
                       WHERE taxonomy_version=? AND model=? AND facet='tag' AND probability >= ?
                       GROUP BY label""",
                        (*key, TAG_THRESHOLD),
                    )
                )
        return {
            "version": self.taxonomy.version,
            "classification_model": self.decision_model,
            "domains": [
                {
                    "id": d.id,
                    "name": d.name,
                    "definition": d.definition,
                    "repos": domain_counts.get(d.id, 0),
                    "tags": [
                        {"id": t.id, "definition": t.definition, "repos": tag_counts.get(t.id, 0)}
                        for t in d.tags
                    ],
                }
                for d in self.taxonomy.domains
            ],
            "kinds": [{"id": k.id, "definition": k.definition} for k in self.taxonomy.kinds],
        }

    def stats(self) -> dict:
        """Headline numbers for the whole catalogue."""
        with self._connect() as conn:
            base = conn.execute(
                """SELECT COUNT(*) AS repos, COUNT(DISTINCT owner) AS accounts,
                          COUNT(DISTINCT country) AS countries, SUM(fork = 0) AS original_repos,
                          SUM(archived = 1) AS archived_repos, MAX(last_scraped) AS last_scraped
                   FROM repositories WHERE COALESCE(current, 1) = 1"""
            ).fetchone()
            languages = conn.execute(
                """SELECT language, COUNT(*) AS repos FROM repositories
                   WHERE COALESCE(current, 1) = 1 AND language IS NOT NULL
                   GROUP BY language ORDER BY repos DESC LIMIT 15"""
            ).fetchall()
            classified = 0
            if self.decision_model:
                classified = conn.execute(
                    """SELECT COUNT(*) FROM decision_results
                       WHERE taxonomy_version=? AND model=? AND error IS NULL""",
                    (self.taxonomy.version, self.decision_model),
                ).fetchone()[0]
        return {
            **dict(base),
            "classified_repos": classified,
            "top_languages": [dict(row) for row in languages],
        }
