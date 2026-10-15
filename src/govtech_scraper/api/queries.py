"""Read-only queries over the published SQLite database.

Shared by the REST API and the MCP tools so both answer the same question the same way.
Every function opens its own read-only connection, so the module is safe to call from
worker threads and can never change the data.
"""

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from ..decision.evidence import source_from_repo
from ..decision.store import row_to_repository
from ..decision.taxonomy import Taxonomy, load_taxonomy
from .places import place

MAX_LIMIT = 100
README_EXCERPT = 600
TAG_THRESHOLD = 0.5
SORTS = {
    "stars": "r.stars DESC",
    "recently_pushed": "r.pushed_at DESC",
    "newest": "r.created_at DESC",
    "name": "r.owner, r.name",
}
# Described, not empty, and pushed again after the day it was created.
NOTABLE_SQL = (
    "(TRIM(COALESCE(r.description, '')) != '' AND COALESCE(r.size_kb, 0) > 0"
    " AND julianday(r.pushed_at) - julianday(r.created_at) > 1)"
)


@dataclass
class Classification:
    model: str
    domain: str | None
    domain_name: str | None
    domain_probability: float | None
    kind: str | None
    confidence_mode: str = "probability"
    tags: list[dict] = field(default_factory=list)
    abstentions: dict = field(default_factory=dict)


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
    readme_excerpt: str | None = None


class Catalogue:
    """Query facade over one database file."""

    def __init__(self, db_path: str | None = None, taxonomy: Taxonomy | None = None):
        self.db_path = db_path or os.getenv("GOVTECH_DB", "govtech.db")
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        self.taxonomy = taxonomy or load_taxonomy()
        self._domain_names = {d.id: d.name for d in self.taxonomy.domains}
        with self._connect() as conn:
            self._has_current = any(
                row["name"] == "current" for row in conn.execute("PRAGMA table_info(repositories)")
            )
            self._has_reuse = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_reuse'"
            ).fetchone() is not None
        self._year_ago: str | None = None
        self.decision_model = os.getenv("DECISION_MODEL") or self._default_decision_model()
        self.confidence_mode = self._model_mode(self.decision_model)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.create_function("evidence_metadata_hash", 20, self._evidence_metadata_hash)
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    @lru_cache(maxsize=100_000)
    def _evidence_metadata_hash(
        html_url,
        owner,
        name,
        country,
        description,
        language,
        topics,
        license,
        default_branch,
        fork,
        archived,
        created_at,
        updated_at,
        pushed_at,
        fork_source,
        stars,
        forks,
        watchers,
        open_issues,
        size_kb,
    ) -> str:
        repo = row_to_repository(
            {
                "html_url": html_url,
                "owner": owner,
                "name": name,
                "country": country,
                "description": description,
                "language": language,
                "topics": topics,
                "license": license,
                "default_branch": default_branch,
                "fork": fork,
                "archived": archived,
                "created_at": created_at,
                "updated_at": updated_at,
                "pushed_at": pushed_at,
                "fork_source": fork_source,
                "stars": stars,
                "forks": forks,
                "watchers": watchers,
                "open_issues": open_issues,
                "size_kb": size_kb,
                "commit_count": None,
            }
        )
        return source_from_repo(repo).metadata_hash

    def year_before_scrape(self) -> str:
        """ISO timestamp 365 days before the latest scrape; the database never changes in-process."""
        if self._year_ago is None:
            with self._connect() as conn:
                last = conn.execute(
                    f"SELECT MAX(last_scraped) FROM repositories WHERE {self.current_predicate()}"
                ).fetchone()[0]
            try:
                ref = datetime.fromisoformat((last or "").replace("Z", "+00:00"))
            except ValueError:
                ref = datetime.now(UTC)
            ref = ref if ref.tzinfo else ref.replace(tzinfo=UTC)
            self._year_ago = (ref - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._year_ago

    def current_predicate(self, alias: str = "") -> str:
        """Legacy publications contain only current rows; never migrate an input database."""
        column = f"{alias}.current" if alias else "current"
        return f"COALESCE({column}, 1) = 1" if self._has_current else "1 = 1"

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
            if self._has_table(conn, "evidence_classifications"):
                row = conn.execute(
                    f"""SELECT model FROM evidence_classifications e
                       JOIN repositories r ON r.html_url = e.html_url
                       WHERE {self.current_predicate("r")} AND taxonomy_version=?
                         AND e.status IN ('labelled', 'abstained', 'failed')
                       GROUP BY model ORDER BY SUM(CASE
                         WHEN e.status IN ('labelled', 'abstained')
                           AND {self._evidence_fresh_sql("e", "r")}
                           AND COALESCE(r.fork, 0) = 0 THEN 1 ELSE 0 END) DESC,
                         COUNT(*) DESC, model LIMIT 1""",
                    (self.taxonomy.version,),
                ).fetchone()
                if row:
                    return row[0]
            if not self._has_table(conn, "decision_results"):
                return None
            row = conn.execute(
                f"""SELECT model FROM decision_results d JOIN repositories r ON r.html_url = d.html_url
                   WHERE {self.current_predicate("r")} AND taxonomy_version=? AND error IS NULL
                   GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1""",
                (self.taxonomy.version,),
            ).fetchone()
        return row[0] if row else None

    def _model_mode(self, model: str | None) -> str | None:
        if not model:
            return None
        with self._connect() as conn:
            if self._has_table(conn, "evidence_classifications"):
                row = conn.execute(
                    """SELECT 1 FROM evidence_classifications
                       WHERE taxonomy_version=? AND model=? LIMIT 1""",
                    (self.taxonomy.version, model),
                ).fetchone()
                if row:
                    return "evidence"
        return "probability"

    # ------------------------------------------------------------------ helpers

    def _evidence_fresh_sql(self, evidence: str, repo: str) -> str:
        current = self._metadata_hash_sql(repo)
        exact = f"{evidence}.metadata_hash = {current}"
        if not self._has_reuse:
            return exact
        return f"""({exact} OR EXISTS (
            SELECT 1 FROM evidence_reuse reuse
            WHERE reuse.html_url={evidence}.html_url
              AND reuse.taxonomy_version={evidence}.taxonomy_version
              AND reuse.model={evidence}.model
              AND reuse.input_hash={evidence}.input_hash
              AND reuse.metadata_hash={current}))"""

    @staticmethod
    def _metadata_hash_sql(alias: str) -> str:
        cols = [
            "html_url",
            "owner",
            "name",
            "country",
            "description",
            "language",
            "topics",
            "license",
            "default_branch",
            "fork",
            "archived",
            "created_at",
            "updated_at",
            "pushed_at",
            "fork_source",
            "stars",
            "forks",
            "watchers",
            "open_issues",
            "size_kb",
        ]
        return "evidence_metadata_hash(" + ",".join(f"{alias}.{c}" for c in cols) + ")"

    def _decision_join(self) -> tuple[str, list]:
        if not self.decision_model:
            return "", []
        if self.confidence_mode == "evidence":
            return (
                f"""LEFT JOIN evidence_classifications d ON d.html_url = r.html_url
                     AND d.taxonomy_version = ? AND d.model = ?
                     AND d.status IN ('labelled', 'abstained')
                     AND {self._evidence_fresh_sql("d", "r")}""",
                [self.taxonomy.version, self.decision_model],
            )
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
        owner: str | None = None,
        maintained: bool = False,
        notable: bool = False,
    ) -> dict:
        """Filter and page through repositories. Returns {"total", "items"}.

        maintained: not archived and pushed in the 12 months before the latest scrape.
        notable: has a description, some content, and a push after the day it was created,
        which hides empty placeholders from lists of new repositories.
        """
        if sort not in SORTS:
            raise ValueError(f"sort must be one of {', '.join(SORTS)}")
        limit = max(1, min(limit, MAX_LIMIT))
        offset = max(0, offset)

        join, params = self._decision_join()
        select_extra = ", d.domain AS d_domain, d.kind AS d_kind" if join else ""
        where = [self.current_predicate("r"), "r.stars >= ?"]
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
        if owner:
            where.append("LOWER(r.owner) = LOWER(?)")
            params.append(owner)
        if not include_forks:
            where.append("r.fork = 0")
        if not include_archived:
            where.append("r.archived = 0")
        if maintained:
            where.append("r.archived = 0 AND r.pushed_at >= ?")
            params.append(self.year_before_scrape())
        if notable:
            where.append(NOTABLE_SQL)
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
                if self.confidence_mode == "evidence":
                    where.append(
                        """EXISTS (SELECT 1 FROM evidence_classification_tags p
                           WHERE p.html_url = r.html_url AND p.taxonomy_version = d.taxonomy_version
                             AND p.model = d.model AND p.tag_id = ?)"""
                    )
                    params.append(tag)
                else:
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
                f"SELECT * FROM repositories WHERE {self.current_predicate()} "
                "AND LOWER(owner)=LOWER(?) AND LOWER(name)=LOWER(?)",
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
            detail.readme_excerpt = self._readme_excerpt(conn, row["html_url"])
            if self.decision_model:
                detail.classification = self._classification(conn, row["html_url"])
                if detail.classification:
                    detail.domain = detail.classification.domain
                    detail.kind = detail.classification.kind
        return asdict(detail)

    def _readme_excerpt(self, conn: sqlite3.Connection, html_url: str) -> str | None:
        """The opening of the README the classifier read, when the publication includes it."""
        if not self._has_table(conn, "evidence_sources"):
            return None
        row = conn.execute(
            "SELECT source_json FROM evidence_sources WHERE html_url=?", (html_url,)
        ).fetchone()
        if row is None:
            return None
        try:
            readme = (json.loads(row[0]).get("fields") or {}).get("readme") or ""
        except (ValueError, AttributeError):
            return None
        text = _plain(readme)
        if not text:
            return None
        return text if len(text) <= README_EXCERPT else text[: README_EXCERPT - 1].rsplit(" ", 1)[0] + "…"

    def _classification(self, conn: sqlite3.Connection, html_url: str) -> Classification | None:
        if self.confidence_mode == "evidence":
            result = conn.execute(
                f"""SELECT d.domain, d.kind, d.abstentions_json FROM evidence_classifications d
                   JOIN repositories r ON r.html_url = d.html_url
                   WHERE d.html_url=? AND d.taxonomy_version=? AND d.model=?
                     AND d.status IN ('labelled', 'abstained')
                     AND {self._evidence_fresh_sql("d", "r")}""",
                (html_url, self.taxonomy.version, self.decision_model),
            ).fetchone()
            if result is None:
                return None
            tags = conn.execute(
                """SELECT tag_id, probability, quote, rationale, source_field
                   FROM evidence_classification_tags
                   WHERE html_url=? AND taxonomy_version=? AND model=?
                   ORDER BY tag_id""",
                (html_url, self.taxonomy.version, self.decision_model),
            ).fetchall()
            return Classification(
                model=self.decision_model,
                domain=result["domain"],
                domain_name=self._domain_names.get(result["domain"]),
                domain_probability=None,
                kind=result["kind"],
                confidence_mode="evidence",
                abstentions=json.loads(result['abstentions_json']),
                tags=[
                    {
                        "id": row["tag_id"],
                        "probability": row["probability"],
                        "quote": row["quote"],
                        "rationale": row["rationale"],
                        "source_field": row["source_field"],
                    }
                    for row in tags
                ],
            )
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
            confidence_mode="probability",
            tags=[
                {"id": label, "probability": round(p, 3)}
                for f, label, p in probs
                if f == "tag" and p >= TAG_THRESHOLD
            ],
        )

    def list_countries(self) -> list[dict]:
        """Registry labels with repository counts, plus the country and level each one means."""
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT country, COUNT(*) AS repos, COUNT(DISTINCT owner) AS accounts,
                          SUM(stars) AS stars
                   FROM repositories WHERE {self.current_predicate()}
                   GROUP BY country ORDER BY repos DESC"""
            ).fetchall()
        return [{**dict(row), **_place_fields(row["country"])} for row in rows]

    def taxonomy_summary(self) -> dict:
        """The taxonomy, with how many repos each domain and tag covers in the current run."""
        domain_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        if self.decision_model:
            key = (self.taxonomy.version, self.decision_model)
            with self._connect() as conn:
                if self.confidence_mode == "evidence":
                    domain_counts = dict(
                        conn.execute(
                            f"""SELECT domain, COUNT(*) FROM evidence_classifications d
                               JOIN repositories r ON r.html_url = d.html_url
                               WHERE {self.current_predicate("r")} AND taxonomy_version=? AND model=?
                                 AND status IN ('labelled', 'abstained') AND domain IS NOT NULL
                                 AND {self._evidence_fresh_sql("d", "r")}
                               GROUP BY domain""",
                            key,
                        )
                    )
                    tag_counts = dict(
                        conn.execute(
                            f"""SELECT tag_id, COUNT(*) FROM evidence_classification_tags p
                               JOIN evidence_classifications d ON d.html_url = p.html_url
                                AND d.taxonomy_version = p.taxonomy_version AND d.model = p.model
                               JOIN repositories r ON r.html_url = p.html_url
                               WHERE {self.current_predicate("r")} AND p.taxonomy_version=? AND p.model=?
                                 AND d.status IN ('labelled', 'abstained')
                                 AND {self._evidence_fresh_sql("d", "r")}
                               GROUP BY tag_id""",
                            key,
                        )
                    )
                else:
                    domain_counts = dict(
                        conn.execute(
                            f"""SELECT domain, COUNT(*) FROM decision_results d JOIN repositories r ON r.html_url = d.html_url
                           WHERE {self.current_predicate("r")} AND taxonomy_version=? AND model=? AND error IS NULL GROUP BY domain""",
                            key,
                        )
                    )
                    tag_counts = dict(
                        conn.execute(
                            f"""SELECT label, COUNT(*) FROM decision_probabilities p JOIN repositories r ON r.html_url = p.html_url
                           WHERE {self.current_predicate("r")} AND taxonomy_version=? AND model=? AND facet='tag' AND probability >= ?
                           GROUP BY label""",
                            (*key, TAG_THRESHOLD),
                        )
                    )
        return {
            "version": self.taxonomy.version,
            "classification_model": self.decision_model,
            "confidence_mode": self.confidence_mode,
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
                f"""SELECT COUNT(*) AS repos, COUNT(DISTINCT owner) AS accounts,
                          COUNT(DISTINCT country) AS countries, SUM(fork = 0) AS original_repos,
                          SUM(archived = 1) AS archived_repos, MAX(last_scraped) AS last_scraped
                   FROM repositories WHERE {self.current_predicate()}"""
            ).fetchone()
            languages = conn.execute(
                f"""SELECT language, COUNT(*) AS repos FROM repositories
                   WHERE {self.current_predicate()} AND language IS NOT NULL
                   GROUP BY language ORDER BY repos DESC LIMIT 15"""
            ).fetchall()
            classified = 0
            if self.decision_model:
                if self.confidence_mode == "evidence":
                    classified = conn.execute(
                        f"""SELECT COUNT(*) FROM evidence_classifications d
                           JOIN repositories r ON r.html_url = d.html_url
                           WHERE {self.current_predicate("r")} AND taxonomy_version=? AND model=?
                             AND status='labelled'
                             AND {self._evidence_fresh_sql("d", "r")}""",
                        (self.taxonomy.version, self.decision_model),
                    ).fetchone()[0]
                else:
                    classified = conn.execute(
                        f"""SELECT COUNT(*) FROM decision_results d JOIN repositories r ON r.html_url = d.html_url
                           WHERE {self.current_predicate("r")} AND taxonomy_version=? AND model=? AND error IS NULL""",
                        (self.taxonomy.version, self.decision_model),
                    ).fetchone()[0]
        return {
            **dict(base),
            "classified_repos": classified,
            "top_languages": [dict(row) for row in languages],
        }


def _place_fields(label: str) -> dict:
    p = place(label)
    return {"slug": p["slug"], "country_name": p["country"], "level": p["level"]}


def _plain(markdown: str) -> str:
    """Rough plain text from README markdown: drop images, HTML, link targets and markup."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", markdown)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[#*_`>|]+", " ", text)
    return " ".join(text.split())
