"""Aggregate views for the Open GovTech Index site: overview, themes and repository context.

A theme is either a COFOG domain (id "00" to "10") or a taxonomy tag (e.g. "digital-identity"),
taken from the stored decision-model run the Catalogue uses. Counts cover original (non-fork)
repositories in the current catalogue.

Growth is measured from creation dates: repositories created in the last 12 months as a share of
those created before then. It needs no snapshot history, so it works on the first scrape, but it
cannot see repositories deleted or made private during the year.

Growth mostly reflects how young a theme is, so momentum uses pace instead: the rate of new
repositories in the last 12 complete months against the 12 before, divided by the same ratio for
every classified repository, so a classification backlog or unclassified sources (other forges)
slow every theme alike and cancel out. Pace comes with an 80% interval and with breadth (how many accounts created the
new work), and together they give each theme a status: emerging, concentrated, cooling or steady.
"""

import math

import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from ..db import CLASSIFIABLE
from .places import LEVELS, place, slug
from .queries import NOTABLE_SQL, TAG_THRESHOLD, Catalogue

BUILDING_BLOCKS = Path(__file__).with_name("building_blocks.yaml")

YEAR = timedelta(days=365)
PACE_MONTHS = 12  # complete months in each pace window
PACE_MIN = 20  # new repositories across both windows before a theme gets a status
PACE_Z = 1.2816  # two-sided 80% interval
PACE_EFFECT = 1.2  # a status also needs pace at least 20% above the index (or 1/1.2 below)
BREADTH_MIN = 5  # accounts creating new work before a rise counts as emerging
CONCENTRATED_SHARE = 50  # top account's share of new work, in percent, above which a rise is one team's
PRACTICE_MIN = 25  # maintained repositories a label needs to appear on the practice table
UNLICENSED = ("", "NOASSERTION", "other", "Other")
FORGE_HOSTS_PATH = Path(__file__).parent.parent / "forge_hosts.yaml"


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


def pace_stats(new: int, prior: int, index_ratio: float | None) -> dict:
    """Pace against the index, with an 80% interval, from new repositories in two equal windows.

    Half a repository is added to each count so empty windows give a wide interval, not a crash.
    """
    if not index_ratio:
        return {"pace": None, "pace_low": None, "pace_high": None}
    a, b = new + 0.5, prior + 0.5
    pace = a / b / index_ratio
    spread = math.exp(PACE_Z * math.sqrt(1 / a + 1 / b))
    return {"pace": round(pace, 3), "pace_low": round(pace / spread, 3), "pace_high": round(pace * spread, 3)}


def pace_status(new: int, prior: int, stats: dict, accounts: int, top_share: float) -> str:
    """emerging, concentrated (rising but one team's work), cooling, steady, or too_small."""
    if stats["pace"] is None or new + prior < PACE_MIN:
        return "too_small"
    # The interval says a change is real; the effect size says it is big enough to notice. Large
    # themes have narrow intervals, so without it a +8% drift would count as emerging.
    if stats["pace_low"] > 1 and stats["pace"] >= PACE_EFFECT:
        return "concentrated" if accounts < BREADTH_MIN or top_share > CONCENTRATED_SHARE else "emerging"
    if stats["pace_high"] < 1 and stats["pace"] <= 1 / PACE_EFFECT:
        return "cooling"
    return "steady"


class Insights:
    """Site-level aggregates over a Catalogue's database and decision run."""

    def __init__(self, catalogue: Catalogue):
        self.catalogue = catalogue
        self.taxonomy = catalogue.taxonomy
        self._domains = {d.id: d for d in self.taxonomy.domains}
        self._tags = {t.id: (t, d) for d in self.taxonomy.domains for t in d.tags}
        # The database never changes while the process runs, so heavy views are computed once.
        self._cache: dict = {}

    def _once(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

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
        if self.catalogue.confidence_mode == "evidence":
            domain_sql = f"""SELECT d.domain AS theme, d.html_url FROM evidence_classifications d
                             JOIN repositories r ON r.html_url = d.html_url
                             WHERE d.taxonomy_version=? AND d.model=?
                               AND d.status IN ('labelled', 'abstained')
                               AND d.domain IS NOT NULL
                               AND {self.catalogue._evidence_fresh_sql("d", "r")}"""
            tag_sql = f"""SELECT p.tag_id AS theme, p.html_url FROM evidence_classification_tags p
                          JOIN evidence_classifications d ON d.html_url = p.html_url
                           AND d.taxonomy_version = p.taxonomy_version AND d.model = p.model
                          JOIN repositories r ON r.html_url = p.html_url
                          WHERE p.taxonomy_version=? AND p.model=?
                            AND d.status IN ('labelled', 'abstained')
                            AND {self.catalogue._evidence_fresh_sql("d", "r")}"""
            if theme is None:
                return f"{domain_sql} UNION ALL {tag_sql}", [version, model, version, model]
            if theme in self._domains:
                return f"{domain_sql} AND d.domain = ?", [version, model, theme]
            return f"{tag_sql} AND p.tag_id = ?", [version, model, theme]
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

    def _licensed_sql(self, alias: str = "") -> str:
        col = f"{alias}.license" if alias else "license"
        return f"({col} IS NOT NULL AND {col} NOT IN ({','.join(repr(u) for u in UNLICENSED)}))"

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
            include_forks=False, include_archived=False, notable=True, sort="newest", limit=6
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
            "sources": self._sources(),
        }

    def _sources(self) -> list[dict]:
        """Current repositories by where they were read: GitHub, curated government servers, or
        submitted accounts on shared servers such as gitlab.com and Codeberg."""
        servers = {str(h["host"]).strip().lower()
                   for h in yaml.safe_load(FORGE_HOSTS_PATH.read_text()).get("hosts", [])}
        counts = {"github": [0, 0], "servers": [0, 0], "submitted": [0, 0]}
        hosts = set()
        with self.catalogue._connect() as conn:
            rows = conn.execute(
                f"""SELECT LOWER(substr(html_url, 9, instr(substr(html_url, 9), '/') - 1)) AS host,
                           COUNT(*) AS repos, SUM(fork = 0) AS original
                    FROM repositories WHERE {self.catalogue.current_predicate()}
                    GROUP BY 1"""
            ).fetchall()
        for r in rows:
            key = "github" if r["host"] == "github.com" else "servers" if r["host"] in servers else "submitted"
            if key == "servers":
                hosts.add(r["host"])
            counts[key][0] += r["repos"]
            counts[key][1] += r["original"] or 0
        return [{"key": k, "repos": n, "original": o, **({"hosts": len(hosts)} if k == "servers" else {})}
                for k, (n, o) in counts.items()]

    def _coverage(self, original: int | None = None) -> dict:
        """Classification progress over current original repos the pipeline can read.

        Repos from other forges are not classified yet (CLASSIFIABLE), so they are left
        out of the denominator rather than shown as pending. `original` is ignored.
        """
        cat = self.catalogue
        classified = 0
        assessed = 0
        failed = 0
        with cat._connect() as conn:
            original = conn.execute(
                f"""SELECT COUNT(*) FROM repositories
                    WHERE {cat.current_predicate()} AND fork = 0 AND {CLASSIFIABLE}"""
            ).fetchone()[0]
        if cat.decision_model:
            with cat._connect() as conn:
                if cat.confidence_mode == "evidence":
                    row = conn.execute(
                        f"""SELECT
                               SUM(d.status IN ('labelled', 'abstained')) AS assessed,
                               SUM(d.status = 'labelled') AS labelled,
                               SUM(d.status = 'failed') AS failed
                            FROM evidence_classifications d
                            JOIN repositories r ON r.html_url = d.html_url
                            WHERE d.taxonomy_version=? AND d.model=?
                              AND {self.catalogue.current_predicate("r")} AND r.fork = 0
                              AND {self.catalogue._evidence_fresh_sql("d", "r")}""",
                        (self.taxonomy.version, cat.decision_model),
                    ).fetchone()
                    assessed = row["assessed"] or 0
                    classified = row["labelled"] or 0
                    failed = row["failed"] or 0
                else:
                    classified = conn.execute(
                        f"""SELECT COUNT(*) FROM decision_results d JOIN repositories r ON r.html_url = d.html_url
                           WHERE d.taxonomy_version=? AND d.model=? AND d.error IS NULL
                             AND {self.catalogue.current_predicate("r")} AND r.fork = 0""",
                        (self.taxonomy.version, cat.decision_model),
                    ).fetchone()[0]
                    assessed = classified
        return {
            "model": cat.decision_model,
            "taxonomy_version": self.taxonomy.version,
            "confidence_mode": cat.confidence_mode,
            "classified_original_repos": classified,
            "assessed_repos": assessed,
            "labelled_repos": classified,
            "unclassified_repos": max(0, original - classified),
            "abstained_repos": max(0, assessed - classified),
            "pending_repos": max(0, original - assessed - failed),
            "failed_repos": failed,
            "current_total_repos": original,
            "coverage": round(classified / original * 100, 1) if original else 0.0,
        }

    def themes(self) -> dict:
        """Every domain and tag with size, growth and spread. Empty until a decision run exists."""
        return self._once("themes", self._themes)

    def _themes(self) -> dict:
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
        momentum = self._momentum()
        for t in items:
            t.update(momentum["themes"].get(t["id"]) or self._no_momentum(len(momentum["months"])))
        return {
            "model": cat.decision_model,
            "taxonomy_version": self.taxonomy.version,
            "confidence_mode": cat.confidence_mode,
            "growth_definition": "Repositories created in the last 12 months as a percentage of "
            "those created before then. Original repositories only.",
            "pace_definition": f"New original repositories in the last {PACE_MONTHS} complete months "
            f"against the {PACE_MONTHS} before, divided by the same ratio for every classified repository. "
            "1.0 keeps pace with the index; pace_low and pace_high are an 80% interval.",
            "status_definition": {
                "emerging": f"80% interval above 1.0, pace at least {PACE_EFFECT}, at least {BREADTH_MIN} "
                f"accounts created new work, none more than {CONCENTRATED_SHARE}% of it.",
                "concentrated": "Rising like an emerging theme, but one account created most of the new work "
                f"or fewer than {BREADTH_MIN} accounts did.",
                "cooling": f"80% interval below 1.0 and pace at most {round(1 / PACE_EFFECT, 2)}.",
                "steady": "Pace not clearly different from the index.",
                "too_small": f"Fewer than {PACE_MIN} new repositories across both windows.",
            },
            "index_pace": momentum["index_ratio"],
            "trend_months": momentum["months"],
            "themes": items,
        }

    @staticmethod
    def _no_momentum(months: int) -> dict:
        return {
            "new_window": 0,
            "new_prior": 0,
            "pace": None,
            "pace_low": None,
            "pace_high": None,
            "accounts_window": 0,
            "countries_window": 0,
            "top_account": None,
            "top_account_share": 0,
            "status": "too_small",
            "trend": [0] * months,
        }

    def _momentum(self) -> dict:
        """Pace, breadth and a monthly trend for every theme, from two grouped queries."""

        def build() -> dict:
            cat = self.catalogue
            with cat._connect() as conn:
                ref = self._ref_date(conn)
                months = _months_back(ref, 2 * PACE_MONTHS + 1)[:-1]  # complete months only
                prior, window = set(months[:PACE_MONTHS]), set(months[PACE_MONTHS:])
                start, end = months[0], _months_back(ref, 1)[0]
                cur = cat.current_predicate("r")
                if not cat.decision_model:
                    return {"months": months, "index_ratio": None, "themes": {}}
                members, params = self._members_sql()
                rows = conn.execute(
                    f"""WITH m AS ({members})
                        SELECT m.theme, substr(r.created_at, 1, 7) AS month, r.owner, r.country, COUNT(*) AS n
                        FROM m JOIN repositories r ON r.html_url = m.html_url
                        WHERE {cur} AND r.fork = 0 AND r.created_at >= ? AND r.created_at < ?
                        GROUP BY 1, 2, 3, 4""",
                    params + [start, end],
                ).fetchall()
            per_month: dict[str, Counter] = {}
            owners: dict[str, Counter] = {}
            countries: dict[str, set] = {}
            for r in rows:
                per_month.setdefault(r["theme"], Counter())[r["month"]] += r["n"]
                if r["month"] in window:
                    owners.setdefault(r["theme"], Counter())[r["owner"]] += r["n"]
                    countries.setdefault(r["theme"], set()).add(r["country"])
            # The index is every classified repository (each has exactly one domain), not every
            # repository: recent repos wait longest for classification and other forges are never
            # classified, so comparing themes with the raw total would make every theme look slower.
            index = Counter()
            for theme, counts in per_month.items():
                if theme in self._domains:
                    index.update(counts)
            i_new = sum(index[m] for m in window)
            i_prior = sum(index[m] for m in prior)
            index_ratio = i_new / i_prior if i_new and i_prior else None
            out = {}
            for theme, counts in per_month.items():
                new = sum(counts[m] for m in window)
                before = sum(counts[m] for m in prior)
                accounts = owners.get(theme, Counter())
                top = accounts.most_common(1)[0] if accounts else (None, 0)
                top_share = round(top[1] / new * 100) if new else 0
                stats = pace_stats(new, before, index_ratio)
                out[theme] = {
                    "new_window": new,
                    "new_prior": before,
                    **stats,
                    "accounts_window": len(accounts),
                    "countries_window": len(countries.get(theme, ())),
                    "top_account": top[0],
                    "top_account_share": top_share,
                    "status": pace_status(new, before, stats, len(accounts), top_share),
                    "trend": [counts[m] for m in months],
                }
            return {
                "months": months,
                "index_ratio": round(index_ratio, 3) if index_ratio else None,
                "themes": out,
            }

        return self._once("momentum", build)

    def theme(self, theme: str) -> dict | None:
        """One theme: summary, monthly new repos, countries, adopters, related themes and repos."""
        if not self._known(theme):
            return None
        return self._once(("theme", theme), lambda: self._theme(theme))

    def _theme(self, theme: str) -> dict:
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
            momentum = self._momentum()
            summary.update(momentum["themes"].get(theme) or self._no_momentum(len(momentum["months"])))
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
            teams = conn.execute(
                base.format(
                    cols="r.owner, r.country, COUNT(*) AS repos, SUM(r.archived = 0 AND r.pushed_at >= ?) AS maintained"
                )
                + " GROUP BY r.owner ORDER BY maintained DESC, repos DESC LIMIT 10",
                [*params, _iso(ref - YEAR)],
            ).fetchall()
            all_members, all_params = self._members_sql()
            related = conn.execute(
                f"""WITH m AS MATERIALIZED ({members}), a AS MATERIALIZED ({all_members})
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
            "teams": [{**dict(t), "maintained": t["maintained"] or 0} for t in teams],
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
                     AND LOWER(fork_source) IN (LOWER(?), LOWER(?)) ORDER BY created_at DESC LIMIT 20""",
                (repo["full_name"], repo["html_url"]),
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
                if r["full_name"] != repo["full_name"] and r["full_name"].rsplit("/", 1)[0].lower() != owner.lower()
            ][:5]
            if similar:
                break
        return {
            "full_name": repo["full_name"],
            "history": [dict(h) for h in history],
            "reused_by": [dict(r) for r in reused_by],
            "similar": similar,
        }

    # ------------------------------------------------------------------ reuse between governments

    def _fork_edges(self) -> list[dict]:
        """Every government fork of another account's government repository."""

        def build() -> list[dict]:
            cur = self.catalogue.current_predicate()
            with self.catalogue._connect() as conn:
                parents = {
                    f"{r['owner']}/{r['name']}".lower(): r
                    for r in conn.execute(
                        f"SELECT owner, name, country FROM repositories WHERE {cur} AND fork = 0"
                    )
                }
                forks = conn.execute(
                    f"""SELECT owner, name, country, fork_source, created_at FROM repositories
                        WHERE {cur} AND fork = 1 AND fork_source IS NOT NULL"""
                ).fetchall()
            edges = []
            for f in forks:
                p = parents.get(f["fork_source"].lower())
                if p is None or p["owner"].lower() == f["owner"].lower():
                    continue
                edges.append(
                    {
                        "parent": f"{p['owner']}/{p['name']}",
                        "parent_owner": p["owner"],
                        "parent_country": p["country"],
                        "child": f"{f['owner']}/{f['name']}",
                        "child_owner": f["owner"],
                        "child_country": f["country"],
                        "created_at": f["created_at"],
                    }
                )
            return edges

        return self._once("edges", build)

    @staticmethod
    def _most_reused(edges: list[dict], limit: int) -> list[dict]:
        by: dict[str, dict] = {}
        for e in edges:
            d = by.setdefault(
                e["parent"],
                {"full_name": e["parent"], "country": e["parent_country"], "a": set(), "c": set()},
            )
            d["a"].add(e["child_owner"].lower())
            d["c"].add(e["child_country"])
        ranked = sorted(by.values(), key=lambda d: (-len(d["a"]), -len(d["c"]), d["full_name"]))
        return [
            {
                "full_name": d["full_name"],
                "country": d["country"],
                "reused_by_accounts": len(d["a"]),
                "reused_in_labels": len(d["c"]),
            }
            for d in ranked[:limit]
        ]

    def reuse(self) -> dict:
        """Which governments fork which governments' code, and the most reused repositories."""
        edges = self._fork_edges()
        cross = [e for e in edges if e["parent_country"] != e["child_country"]]
        flows = Counter((e["parent_country"], e["child_country"]) for e in cross)
        reusers = Counter((e["child_owner"], e["child_country"]) for e in edges)
        return {
            "definition": "A government fork of a repository owned by a different government account "
            "in the index. Forks within one account are not counted.",
            "forks": len(edges),
            "cross_border_forks": len(cross),
            "accounts_reusing": len({e["child_owner"].lower() for e in edges}),
            "flows": [{"from": a, "to": b, "forks": n} for (a, b), n in flows.most_common(40)],
            "most_reused": self._most_reused(edges, 20),
            "most_reused_across_borders": self._most_reused(cross, 10),
            "top_reusers": [
                {"owner": o, "country": c, "forks": n} for (o, c), n in reusers.most_common(10)
            ],
        }

    # ------------------------------------------------------------------ countries and accounts

    def _labels(self) -> list[str]:
        def build():
            with self.catalogue._connect() as conn:
                return [
                    r[0]
                    for r in conn.execute(
                        f"SELECT DISTINCT country FROM repositories WHERE {self.catalogue.current_predicate()}"
                    )
                ]

        return self._once("labels", build)

    def label_for(self, slug_: str) -> str | None:
        return next((label for label in self._labels() if slug(label) == slug_), None)

    def _theme_split(self, conn: sqlite3.Connection, column: str, value: str) -> dict[str, tuple[int, int]]:
        """{theme: (repos where column = value, repos everywhere)} over original repositories."""
        members, params = self._members_sql()
        rows = conn.execute(
            f"""WITH m AS ({members})
                SELECT m.theme, SUM(LOWER(r.{column}) = LOWER(?)) AS here, COUNT(*) AS everywhere
                FROM m JOIN repositories r ON r.html_url = m.html_url
                WHERE {self.catalogue.current_predicate("r")} AND r.fork = 0
                GROUP BY m.theme""",
            params + [value],
        ).fetchall()
        return {r["theme"]: (r["here"] or 0, r["everywhere"]) for r in rows if self._known(r["theme"])}

    def _mix(self, split: dict[str, tuple[int, int]]) -> dict:
        """Theme shares for one place against the whole index. Domains sum to 100%."""
        here_total = sum(h for t, (h, _) in split.items() if t in self._domains)
        all_total = sum(a for t, (_, a) in split.items() if t in self._domains)
        if not here_total:
            return {"labelled": 0, "domains": [], "tags": []}

        def row(theme: str, here: int, everywhere: int) -> dict:
            share = here / here_total * 100
            index_share = everywhere / all_total * 100 if all_total else 0
            return {
                **{k: v for k, v in self._describe(theme).items() if k in ("id", "type", "name", "domain")},
                "repos": here,
                "share": round(share, 1),
                "index_share": round(index_share, 1),
                "ratio": round(share / index_share, 2) if index_share else None,
            }

        domains = [row(t, h, a) for t, (h, a) in split.items() if t in self._domains and h]
        tags = [row(t, h, a) for t, (h, a) in split.items() if t in self._tags and h]
        domains.sort(key=lambda r: -r["repos"])
        tags.sort(key=lambda r: -r["repos"])
        # Specialisms: tags this place has proportionally far more of than the index.
        special = [t for t in tags if t["repos"] >= 5 and (t["ratio"] or 0) >= 1.5]
        special.sort(key=lambda r: -(r["ratio"] or 0))
        return {"labelled": here_total, "domains": domains, "tags": tags[:16], "specialisms": special[:8]}

    def countries(self) -> dict:
        """Every registry label with its country, level of government and activity."""

        def build():
            cat = self.catalogue
            with cat._connect() as conn:
                ref = self._ref_date(conn)
                year_ago = _iso(ref - YEAR)
                rows = conn.execute(
                    f"""SELECT country AS label, COUNT(*) AS repos, SUM(fork = 0) AS original,
                              SUM(fork = 0 AND archived = 0 AND pushed_at >= ?) AS maintained,
                              SUM(fork = 0 AND created_at >= ?) AS new_12m,
                              SUM(fork = 0 AND archived = 0 AND pushed_at >= ? AND {self._licensed_sql()}) AS maintained_licensed,
                              COUNT(DISTINCT owner) AS accounts, SUM(stars) AS stars
                       FROM repositories WHERE {cat.current_predicate()}
                       GROUP BY country ORDER BY maintained DESC""",
                    [year_ago, year_ago, year_ago],
                ).fetchall()
            items = []
            for r in rows:
                m = r["maintained"] or 0
                items.append(
                    {
                        **place(r["label"]),
                        "repos": r["repos"],
                        "original": r["original"] or 0,
                        "maintained": m,
                        "new_12m": r["new_12m"] or 0,
                        "accounts": r["accounts"],
                        "stars": r["stars"] or 0,
                        "licensed_share": round((r["maintained_licensed"] or 0) / m * 100, 1) if m else None,
                    }
                )
            return {"levels": list(LEVELS), "as_of": _iso(ref), "items": items}

        return self._once("countries", build)

    def country(self, slug_: str) -> dict | None:
        """One registry label: activity, theme mix against the index, accounts and reuse."""
        label = self.label_for(slug_)
        if label is None:
            return None
        return self._once(("country", label), lambda: self._country(label))

    def _country(self, label: str) -> dict:
        cat = self.catalogue
        summary = next(c for c in self.countries()["items"] if c["label"] == label)
        with cat._connect() as conn:
            ref = self._ref_date(conn)
            year_ago = _iso(ref - YEAR)
            cur = cat.current_predicate()
            per_month = dict(
                conn.execute(
                    f"""SELECT substr(created_at, 1, 7), COUNT(*) FROM repositories
                        WHERE {cur} AND fork = 0 AND country = ? GROUP BY 1""",
                    (label,),
                ).fetchall()
            )
            accounts = conn.execute(
                f"""SELECT owner, COUNT(*) AS repos, SUM(archived = 0 AND pushed_at >= ?) AS maintained,
                          SUM(stars) AS stars
                   FROM repositories WHERE {cur} AND fork = 0 AND country = ?
                   GROUP BY owner ORDER BY maintained DESC, repos DESC LIMIT 12""",
                (year_ago, label),
            ).fetchall()
            languages = conn.execute(
                f"""SELECT language, COUNT(*) AS repos FROM repositories
                   WHERE {cur} AND fork = 0 AND archived = 0 AND pushed_at >= ? AND country = ?
                     AND language IS NOT NULL
                   GROUP BY language ORDER BY repos DESC LIMIT 8""",
                (year_ago, label),
            ).fetchall()
            mix = self._mix(self._theme_split(conn, "country", label)) if cat.decision_model else None
        edges = self._fork_edges()
        out = [e for e in edges if e["parent_country"] == label and e["child_country"] != label]
        into = [e for e in edges if e["child_country"] == label and e["parent_country"] != label]
        return {
            **summary,
            "as_of": _iso(ref),
            "monthly_new": [{"month": m, "new": per_month.get(m, 0)} for m in _months_back(ref, 24)],
            "accounts_top": [{**dict(a), "maintained": a["maintained"] or 0} for a in accounts],
            "languages": [dict(x) for x in languages],
            "theme_mix": mix,
            "reused_by": [
                {"label": c, "forks": n} for c, n in Counter(e["child_country"] for e in out).most_common(8)
            ],
            "reuses_from": [
                {"label": c, "forks": n} for c, n in Counter(e["parent_country"] for e in into).most_common(8)
            ],
            "most_reused": self._most_reused(out, 6),
            "top_repos": cat.search_repos(
                country=label, include_forks=False, maintained=True, sort="stars", limit=8
            )["items"],
            "newest_repos": cat.search_repos(
                country=label, include_forks=False, include_archived=False, notable=True, sort="newest", limit=6
            )["items"],
        }

    def account(self, owner: str) -> dict | None:
        """One government GitHub account: activity, themes, languages and reuse."""
        cat = self.catalogue
        with cat._connect() as conn:
            ref = self._ref_date(conn)
            year_ago = _iso(ref - YEAR)
            cur = cat.current_predicate()
            row = conn.execute(
                f"""SELECT MIN(owner) AS owner, MIN(country) AS country, COUNT(*) AS repos,
                          SUM(fork = 0) AS original,
                          SUM(fork = 0 AND archived = 0 AND pushed_at >= ?) AS maintained,
                          SUM(fork = 0 AND {self._licensed_sql()}) AS licensed,
                          SUM(stars) AS stars, MIN(created_at) AS first_repo, MAX(pushed_at) AS last_push
                   FROM repositories WHERE {cur} AND LOWER(owner) = LOWER(?)""",
                (year_ago, owner),
            ).fetchone()
            if not row["repos"]:
                return None
            owner = row["owner"]
            per_month = dict(
                conn.execute(
                    f"""SELECT substr(created_at, 1, 7), COUNT(*) FROM repositories
                        WHERE {cur} AND fork = 0 AND owner = ? GROUP BY 1""",
                    (owner,),
                ).fetchall()
            )
            languages = conn.execute(
                f"""SELECT language, COUNT(*) AS repos FROM repositories
                   WHERE {cur} AND fork = 0 AND owner = ? AND language IS NOT NULL
                   GROUP BY language ORDER BY repos DESC LIMIT 6""",
                (owner,),
            ).fetchall()
            mix = self._mix(self._theme_split(conn, "owner", owner)) if cat.decision_model else None
            kind = None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'").fetchone():
                k = conn.execute(
                    "SELECT account_type FROM accounts WHERE LOWER(username) = LOWER(?)", (owner,)
                ).fetchone()
                kind = k[0] if k else None
        edges = self._fork_edges()
        lo = owner.lower()
        out = [e for e in edges if e["parent_owner"].lower() == lo]
        into = [e for e in edges if e["child_owner"].lower() == lo]
        original = row["original"] or 0
        return {
            "owner": owner,
            "account_type": kind,
            # Owners from other forges are "host/group", which is also their web address.
            "url": f"https://{owner}" if "." in owner.split("/")[0] else f"https://github.com/{owner}",
            "github_url": None if "." in owner.split("/")[0] else f"https://github.com/{owner}",
            **{k: v for k, v in place(row["country"]).items() if k != "label"},
            "label": row["country"],
            "repos": row["repos"],
            "original": original,
            "maintained": row["maintained"] or 0,
            "stars": row["stars"] or 0,
            "licensed_share": round((row["licensed"] or 0) / original * 100, 1) if original else None,
            "first_repo": row["first_repo"],
            "last_push": row["last_push"],
            "as_of": _iso(ref),
            "monthly_new": [{"month": m, "new": per_month.get(m, 0)} for m in _months_back(ref, 24)],
            "languages": [dict(x) for x in languages],
            "theme_mix": mix,
            "reused_by": [
                {"owner": o, "country": c, "forks": n}
                for (o, c), n in Counter((e["child_owner"], e["child_country"]) for e in out).most_common(10)
            ],
            "reuses_from": [
                {"owner": o, "country": c, "forks": n}
                for (o, c), n in Counter((e["parent_owner"], e["parent_country"]) for e in into).most_common(10)
            ],
            "most_reused": self._most_reused(out, 5),
            "top_repos": cat.search_repos(owner=owner, include_forks=False, sort="stars", limit=10)["items"],
            "recent_repos": cat.search_repos(
                owner=owner, include_forks=False, include_archived=False, sort="recently_pushed", limit=6
            )["items"],
        }

    # ------------------------------------------------------------------ this week

    def week(self) -> dict:
        """What changed in the latest weekly scrape."""
        return self._once("week", self._week)

    def _week(self) -> dict:
        cat = self.catalogue
        cur = cat.current_predicate("r")
        with cat._connect() as conn:
            ref = self._ref_date(conn)
            week_ago = _iso(ref - timedelta(days=7))
            counts = conn.execute(
                f"""SELECT SUM(r.created_at >= ?) AS new, SUM(r.created_at >= ? AND {NOTABLE_SQL}) AS notable
                    FROM repositories r WHERE {cur} AND r.fork = 0""",
                (week_ago, week_ago),
            ).fetchone()
            archived = conn.execute(
                f"""SELECT r.owner || '/' || r.name AS full_name, r.country, r.description, r.stars,
                           r.updated_at
                    FROM repositories r WHERE {cur} AND r.fork = 0 AND r.archived = 1 AND r.updated_at >= ?
                    ORDER BY r.stars DESC LIMIT 10""",
                (week_ago,),
            ).fetchall()
            archived_n = conn.execute(
                f"""SELECT COUNT(*) FROM repositories r
                    WHERE {cur} AND r.fork = 0 AND r.archived = 1 AND r.updated_at >= ?""",
                (week_ago,),
            ).fetchone()[0]
            movers, since = self._star_movers(conn)
        notable = [
            r
            for r in cat.search_repos(
                include_forks=False, include_archived=False, notable=True, sort="newest", limit=40
            )["items"]
            if (r["created_at"] or "") >= week_ago
        ]
        fastest = [
            t for t in self.themes()["themes"] if t["type"] == "tag" and t["status"] in ("emerging", "concentrated")
        ]
        fastest.sort(key=lambda t: -(t["pace"] or 0))
        return {
            "as_of": _iso(ref),
            "week_start": week_ago,
            "new": counts["new"] or 0,
            "new_notable": counts["notable"] or 0,
            "notable": notable,
            "archived": {"count": archived_n, "items": [dict(a) for a in archived]},
            "star_movers": {"since": since, "items": movers},
            "fastest_themes": fastest[:8],
            "definitions": {
                "notable": "Has a description, some content, and a push after the day it was created.",
                "archived": "Archived and last updated on GitHub in the past seven days.",
                "star_movers": "Stars gained between the latest weekly snapshot and the one before it.",
            },
        }

    def _star_movers(self, conn: sqlite3.Connection) -> tuple[list[dict], str | None]:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='repository_snapshots'"
        ).fetchone():
            return [], None
        latest = conn.execute("SELECT MAX(scrape_timestamp) FROM repository_snapshots").fetchone()[0]
        latest_dt = _parse(latest)
        if latest_dt is None:
            return [], None
        latest_day = latest_dt.strftime("%Y-%m-%d")
        prev = conn.execute(
            "SELECT MAX(scrape_timestamp) FROM repository_snapshots WHERE scrape_timestamp < ?",
            (_iso(latest_dt - timedelta(days=5)),),
        ).fetchone()[0]
        prev_dt = _parse(prev)
        if prev_dt is None:
            return [], None
        prev_day = prev_dt.strftime("%Y-%m-%d")

        def day(d: str) -> str:
            return f"""SELECT html_url, MAX(stars) AS stars FROM repository_snapshots
                       WHERE scrape_timestamp >= '{d}' AND scrape_timestamp < date('{d}', '+1 day')
                       GROUP BY html_url"""

        rows = conn.execute(
            f"""SELECT r.owner || '/' || r.name AS full_name, r.country, r.description,
                       a.stars AS stars, a.stars - b.stars AS gained
                FROM ({day(latest_day)}) a JOIN ({day(prev_day)}) b ON a.html_url = b.html_url
                JOIN repositories r ON r.html_url = a.html_url
                WHERE {self.catalogue.current_predicate("r")} AND r.fork = 0 AND a.stars > b.stars
                ORDER BY gained DESC LIMIT 10"""
        ).fetchall()
        return [dict(r) for r in rows], prev_day

    # ------------------------------------------------------------------ open-source practice

    def practice(self) -> dict:
        """Signals of open-source practice by registry label, from metadata already scraped."""

        def build():
            cat = self.catalogue
            with cat._connect() as conn:
                ref = self._ref_date(conn)
                year_ago = _iso(ref - YEAR)
                maint = "archived = 0 AND pushed_at >= ?"
                cols = f"""COUNT(*) AS original,
                    SUM({maint}) AS maintained,
                    SUM({maint} AND {self._licensed_sql()}) AS licensed,
                    SUM({maint} AND TRIM(COALESCE(description, '')) != '') AS described,
                    SUM({maint} AND topics IS NOT NULL AND topics NOT IN ('', '[]')) AS topics,
                    SUM(archived = 1) AS archived"""
                where = f"WHERE {cat.current_predicate()} AND fork = 0"
                rows = conn.execute(
                    f"SELECT country AS label, {cols} FROM repositories {where} GROUP BY country",
                    [year_ago] * 4,
                ).fetchall()
                total = conn.execute(f"SELECT {cols} FROM repositories {where}", [year_ago] * 4).fetchone()

            def shares(r) -> dict:
                m = r["maintained"] or 0
                pc = lambda n, d: round((n or 0) / d * 100, 1) if d else None  # noqa: E731
                out = {
                    "maintained": m,
                    "licensed_share": pc(r["licensed"], m),
                    "described_share": pc(r["described"], m),
                    "topics_share": pc(r["topics"], m),
                    "archived_share": pc(r["archived"], r["original"]),
                }
                parts = [out["licensed_share"], out["described_share"], out["topics_share"]]
                out["score"] = round(sum(parts) / 3, 1) if None not in parts else None
                return out

            items = [{**place(r["label"]), **shares(r)} for r in rows if (r["maintained"] or 0) >= PRACTICE_MIN]
            items.sort(key=lambda x: -(x["score"] or 0))
            return {
                "as_of": _iso(ref),
                "minimum_maintained": PRACTICE_MIN,
                "index": shares(total),
                "items": items,
                "definitions": {
                    "licensed_share": "Maintained original repositories with a licence GitHub can detect.",
                    "described_share": "Maintained original repositories with a description.",
                    "topics_share": "Maintained original repositories with at least one GitHub topic.",
                    "archived_share": "Original repositories archived by their owner, a sign of tidy retirement.",
                    "score": "Average of the licensed, described and topics shares.",
                },
            }

        return self._once("practice", build)

    # ------------------------------------------------------------------ building blocks

    def building_blocks(self) -> dict:
        """Hand-picked reusable components per capability, with live figures from the index."""

        def build():
            spec = yaml.safe_load(BUILDING_BLOCKS.read_text())
            cat = self.catalogue
            reused = Counter()
            for e in self._fork_edges():
                reused[e["parent"].lower()] += 1
            groups = []
            for g in spec["groups"]:
                picks = []
                for item in g["repos"]:
                    owner, name = item["repo"].split("/", 1)
                    r = cat.get_repo(owner, name)
                    if r is None:
                        continue
                    picks.append(
                        {
                            "full_name": r["full_name"],
                            "html_url": r["html_url"],
                            "country": r["country"],
                            "description": r["description"],
                            "note": item.get("note"),
                            "language": r["language"],
                            "stars": r["stars"],
                            "license": r["license"],
                            "archived": r["archived"],
                            "pushed_at": r["pushed_at"],
                            "government_forks": reused[r["full_name"].lower()],
                        }
                    )
                more = []
                if cat.decision_model and g.get("tag") in self._tags:
                    picked = {p["full_name"].lower() for p in picks}
                    more = [
                        r
                        for r in cat.search_repos(
                            tag=g["tag"], include_forks=False, maintained=True, sort="stars", limit=10
                        )["items"]
                        if r["full_name"].lower() not in picked
                    ][:5]
                groups.append(
                    {
                        "id": g["id"],
                        "name": g["name"],
                        "summary": g["summary"],
                        "tag": g.get("tag"),
                        "picks": picks,
                        "also_popular": more,
                    }
                )
            return {"reviewed": spec.get("reviewed"), "groups": groups}

        return self._once("blocks", build)
