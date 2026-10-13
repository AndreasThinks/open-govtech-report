"""Aggregate views for the Open GovTech Index site: overview, themes and repository context.

A theme is either a COFOG domain (id "00" to "10") or a taxonomy tag (e.g. "digital-identity"),
taken from the stored decision-model run the Catalogue uses. Counts cover original (non-fork)
repositories in the current catalogue.

Growth is measured from creation dates: repositories created in the last 12 months as a share of
those created before then. It needs no snapshot history, so it works on the first scrape, but it
cannot see repositories deleted or made private during the year.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

from .queries import TAG_THRESHOLD, Catalogue

YEAR = timedelta(days=365)
UNLICENSED = ("", "NOASSERTION", "other", "Other")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _months_back(ref: datetime, n: int) -> list[str]:
    """The n calendar months ending with ref's month, oldest first, as YYYY-MM."""
    y, m = ref.year, ref.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


_ACRONYMS = {"ai": "AI", "api": "API", "foi": "FOI", "hr": "HR"}


def tag_name(tag_id: str) -> str:
    """A readable name for a tag id: "ai-and-machine-learning" -> "AI and machine learning"."""
    words = [_ACRONYMS.get(w, w) for w in tag_id.split("-")]
    words[0] = words[0][0].upper() + words[0][1:]
    return " ".join(words)


def _growth(now: int, before: int) -> float | None:
    return round((now - before) / before * 100, 1) if before else None


class Insights:
    """Site-level aggregates over a Catalogue's database and decision run."""

    def __init__(self, catalogue: Catalogue):
        self.catalogue = catalogue
        self.taxonomy = catalogue.taxonomy
        self._domains = {d.id: d for d in self.taxonomy.domains}
        self._tags = {t.id: (t, d) for d in self.taxonomy.domains for t in d.tags}

    # ------------------------------------------------------------------ helpers

    def _ref_date(self, conn: sqlite3.Connection) -> datetime:
        """The date of the latest scrape, so a stale database gives stable, sensible numbers."""
        row = conn.execute(
            f"SELECT MAX(last_scraped) FROM repositories WHERE {self.catalogue.current_predicate()}"
        ).fetchone()
        return _parse(row[0]) or datetime.now(UTC)

    def _members_sql(self, theme: str | None = None) -> tuple[str, list]:
        """SQL selecting (theme, html_url) pairs from the active decision run."""
        model, version = self.catalogue.decision_model, self.taxonomy.version
        domain_sql = """SELECT domain AS theme, html_url FROM decision_results
                        WHERE taxonomy_version=? AND model=? AND error IS NULL AND domain IS NOT NULL"""
        tag_sql = """SELECT label AS theme, html_url FROM decision_probabilities
                     WHERE taxonomy_version=? AND model=? AND facet='tag' AND probability >= ?"""
        if theme is None:
            return f"{domain_sql} UNION ALL {tag_sql}", [
                version,
                model,
                version,
                model,
                TAG_THRESHOLD,
            ]
        if theme in self._domains:
            return f"{domain_sql} AND domain = ?", [version, model, theme]
        return f"{tag_sql} AND label = ?", [version, model, TAG_THRESHOLD, theme]

    def _describe(self, theme: str) -> dict:
        if theme in self._domains:
            d = self._domains[theme]
            return {
                "id": d.id,
                "type": "domain",
                "name": d.name,
                "definition": d.definition,
                "domain": d.id,
                "domain_name": d.name,
            }
        t, d = self._tags[theme]
        return {
            "id": t.id,
            "type": "tag",
            "name": tag_name(t.id),
            "definition": t.definition,
            "domain": d.id,
            "domain_name": d.name,
        }

    def _theme_rows(self, conn: sqlite3.Connection, ref: datetime, theme: str | None = None):
        members, params = self._members_sql(theme)
        year_ago = _iso(ref - YEAR)
        return conn.execute(
            f"""WITH m AS ({members})
                SELECT m.theme,
                       COUNT(*) AS repos,
                       SUM(r.archived = 0 AND r.pushed_at >= ?) AS maintained,
                       SUM(r.created_at >= ?) AS new_12m,
                       SUM(r.created_at < ?) AS before_12m,
                       COUNT(DISTINCT r.country) AS countries,
                       COUNT(DISTINCT r.owner) AS accounts,
                       SUM(r.license IS NOT NULL AND r.license NOT IN ({",".join("?" * len(UNLICENSED))})) AS licensed,
                       SUM(r.stars) AS stars
                FROM m JOIN repositories r ON r.html_url = m.html_url
                WHERE {self.catalogue.current_predicate("r")} AND r.fork = 0
                GROUP BY m.theme""",
            params + [year_ago, year_ago, year_ago, *UNLICENSED],
        ).fetchall()

    def _summary(self, row: sqlite3.Row) -> dict:
        return {
            **self._describe(row["theme"]),
            "repos": row["repos"],
            "maintained": row["maintained"] or 0,
            "new_12m": row["new_12m"] or 0,
            "growth_12m": _growth(row["repos"], row["before_12m"] or 0),
            "countries": row["countries"],
            "accounts": row["accounts"],
            "licensed_share": round((row["licensed"] or 0) / row["repos"] * 100, 1),
            "stars": row["stars"] or 0,
        }

    def _known(self, theme: str) -> bool:
        return theme in self._domains or theme in self._tags

    # ------------------------------------------------------------------ views

    def overview(self) -> dict:
        """Headline numbers, the catalogue over time, status breakdown, countries and newest repos."""
        cat = self.catalogue
        with cat._connect() as conn:
            ref = self._ref_date(conn)
            year_ago, week_ago = _iso(ref - YEAR), _iso(ref - timedelta(days=7))
            month_ago = _iso(ref - timedelta(days=30))
            unl = ",".join("?" * len(UNLICENSED))
            t = conn.execute(
                f"""SELECT COUNT(*) AS repos,
                          SUM(fork = 0) AS original,
                          SUM(fork = 0 AND archived = 0 AND pushed_at >= ?) AS maintained,
                          SUM(fork = 0 AND archived = 0 AND pushed_at >= ?
                              AND license IS NOT NULL AND license NOT IN ({unl})) AS maintained_licensed,
                          SUM(fork = 0 AND created_at >= ?) AS new_7d,
                          SUM(fork = 0 AND created_at >= ?) AS new_30d,
                          SUM(fork = 0 AND created_at >= ?) AS new_12m,
                          SUM(fork = 0 AND archived = 0 AND (pushed_at < ? OR pushed_at IS NULL)) AS dormant,
                          SUM(fork = 0 AND archived = 1) AS archived,
                          COUNT(DISTINCT owner) AS accounts,
                          COUNT(DISTINCT country) AS countries,
                          MAX(last_scraped) AS last_scraped
                   FROM repositories WHERE {self.catalogue.current_predicate()}""",
                [year_ago, year_ago, *UNLICENSED, week_ago, month_ago, year_ago, year_ago],
            ).fetchone()
            per_month = dict(
                conn.execute(
                    f"""SELECT substr(created_at, 1, 7), COUNT(*) FROM repositories
                       WHERE {self.catalogue.current_predicate()} AND fork = 0 AND created_at IS NOT NULL
                       GROUP BY 1"""
                ).fetchall()
            )
            countries = conn.execute(
                f"""SELECT country,
                           SUM(archived = 0 AND pushed_at >= ?) AS maintained,
                           SUM(license IS NOT NULL AND license NOT IN ({unl})) * 100.0 / COUNT(*) AS licensed_share,
                           COUNT(DISTINCT owner) AS accounts
                    FROM repositories WHERE {self.catalogue.current_predicate()} AND fork = 0
                    GROUP BY country ORDER BY maintained DESC LIMIT 8""",
                [year_ago, *UNLICENSED],
            ).fetchall()

        months = _months_back(ref, 36)
        running = sum(n for m, n in per_month.items() if m < months[0])
        timeline = []
        for m in months:
            running += per_month.get(m, 0)
            timeline.append({"month": m, "new": per_month.get(m, 0), "total": running})

        original = t["original"] or 0
        maintained = t["maintained"] or 0
        newest = cat.search_repos(
            include_forks=False, include_archived=False, sort="newest", limit=6
        )
        return {
            "as_of": t["last_scraped"],
            "totals": {
                "repos": t["repos"],
                "original": original,
                "maintained": maintained,
                "accounts": t["accounts"],
                "countries": t["countries"],
                "new_7d": t["new_7d"] or 0,
                "new_30d": t["new_30d"] or 0,
                "licensed_share": round((t["maintained_licensed"] or 0) / maintained * 100, 1)
                if maintained
                else None,
            },
            "status": {
                "new_12m": t["new_12m"] or 0,
                "active": maintained,
                "dormant": t["dormant"] or 0,
                "archived": t["archived"] or 0,
            },
            "timeline": timeline,
            "countries": [
                {**dict(c), "licensed_share": round(c["licensed_share"] or 0, 1)} for c in countries
            ],
            "newest": newest["items"],
            "classification": self._coverage(original),
        }

    def _coverage(self, original: int) -> dict:
        cat = self.catalogue
        classified = 0
        if cat.decision_model:
            with cat._connect() as conn:
                classified = conn.execute(
                    f"""SELECT COUNT(*) FROM decision_results d JOIN repositories r ON r.html_url = d.html_url
                       WHERE d.taxonomy_version=? AND d.model=? AND d.error IS NULL
                         AND {self.catalogue.current_predicate("r")} AND r.fork = 0""",
                    (self.taxonomy.version, cat.decision_model),
                ).fetchone()[0]
        return {
            "model": cat.decision_model,
            "taxonomy_version": self.taxonomy.version,
            "classified_original_repos": classified,
            "coverage": round(classified / original * 100, 1) if original else 0.0,
        }

    def themes(self) -> dict:
        """Every domain and tag with size, growth and spread. Empty until a decision run exists."""
        cat = self.catalogue
        items: list[dict] = []
        if cat.decision_model:
            with cat._connect() as conn:
                ref = self._ref_date(conn)
                items = [
                    self._summary(row)
                    for row in self._theme_rows(conn, ref)
                    if self._known(row["theme"])
                ]
        items.sort(key=lambda t: (t["type"] != "domain", -t["repos"]))
        return {
            "model": cat.decision_model,
            "taxonomy_version": self.taxonomy.version,
            "growth_definition": "Repositories created in the last 12 months as a percentage of "
            "those created before then. Original repositories only.",
            "themes": items,
        }

    def theme(self, theme: str) -> dict | None:
        """One theme: summary, monthly new repos, countries, adopters, related themes and repos."""
        if not self._known(theme):
            return None
        cat = self.catalogue
        if not cat.decision_model:
            return {**self._describe(theme), "repos": 0, "model": None}
        members, params = self._members_sql(theme)
        with cat._connect() as conn:
            ref = self._ref_date(conn)
            rows = self._theme_rows(conn, ref, theme)
            if not rows:
                return {**self._describe(theme), "repos": 0, "model": cat.decision_model}
            summary = self._summary(rows[0])
            base = f"""WITH m AS ({members})
                       SELECT {{cols}} FROM m JOIN repositories r ON r.html_url = m.html_url
                       WHERE {self.catalogue.current_predicate("r")} AND r.fork = 0"""
            per_month = dict(
                conn.execute(
                    base.format(cols="substr(r.created_at, 1, 7), COUNT(*)") + " GROUP BY 1", params
                ).fetchall()
            )
            countries = conn.execute(
                base.format(cols="r.country, COUNT(*) AS repos")
                + " GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
                params,
            ).fetchall()
            adopters = conn.execute(
                base.format(cols="r.owner, r.country, MIN(r.created_at) AS first_repo")
                + " AND r.created_at IS NOT NULL GROUP BY r.owner ORDER BY first_repo DESC LIMIT 15",
                params,
            ).fetchall()
            all_members, all_params = self._members_sql()
            related = conn.execute(
                f"""WITH m AS ({members}), a AS ({all_members})
                    SELECT a.theme, COUNT(*) AS shared FROM m JOIN a ON a.html_url = m.html_url
                    JOIN repositories r ON r.html_url = m.html_url
                    WHERE a.theme != ? AND {self.catalogue.current_predicate("r")} AND r.fork = 0
                    GROUP BY a.theme ORDER BY shared DESC LIMIT 12""",
                params + all_params + [theme],
            ).fetchall()

        key = "domain" if theme in self._domains else "tag"
        top = cat.search_repos(**{key: theme}, include_forks=False, sort="stars", limit=12)
        newest = cat.search_repos(**{key: theme}, include_forks=False, sort="newest", limit=6)
        months = _months_back(ref, 24)
        return {
            **summary,
            "model": cat.decision_model,
            "as_of": _iso(ref),
            "monthly_new": [{"month": m, "new": per_month.get(m, 0)} for m in months],
            "by_country": [dict(c) for c in countries],
            "adopters": [dict(a) for a in reversed(adopters)],
            "related": [
                {
                    **self._describe(r["theme"]),
                    "shared": r["shared"],
                    "share": round(r["shared"] / summary["repos"] * 100, 1),
                }
                for r in related
                if self._known(r["theme"]) and r["theme"] not in self._domains
            ][:8],
            "top_repos": top["items"],
            "newest_repos": newest["items"],
        }

    def repo_context(self, owner: str, name: str) -> dict | None:
        """Snapshot history, government forks and similar repositories for one repository."""
        cat = self.catalogue
        repo = cat.get_repo(owner, name)
        if repo is None:
            return None
        with cat._connect() as conn:
            history = conn.execute(
                """SELECT substr(scrape_timestamp, 1, 10) AS date, MAX(stars) AS stars, MAX(forks) AS forks
                   FROM repository_snapshots WHERE html_url = ? GROUP BY 1 ORDER BY 1""",
                (repo["html_url"],),
            ).fetchall()
            reused_by = conn.execute(
                f"""SELECT owner || '/' || name AS full_name, owner, country, created_at
                   FROM repositories WHERE {self.catalogue.current_predicate()} AND fork = 1
                     AND LOWER(fork_source) = LOWER(?) ORDER BY created_at DESC LIMIT 20""",
                (repo["full_name"],),
            ).fetchall()

        similar: list[dict] = []
        cls = repo.get("classification") or {}
        filters = [{"tag": t["id"]} for t in cls.get("tags", [])[:1]]
        if cls.get("domain"):
            filters.append({"domain": cls["domain"]})
        for f in filters:
            found = cat.search_repos(**f, include_forks=False, include_archived=False, limit=12)
            similar = [
                r
                for r in found["items"]
                if r["full_name"] != repo["full_name"] and r["full_name"].split("/")[0] != owner
            ][:5]
            if similar:
                break
        return {
            "full_name": repo["full_name"],
            "history": [dict(h) for h in history],
            "reused_by": [dict(r) for r in reused_by],
            "similar": similar,
        }
