"""Shareable page paths, RSS feeds, README badges and the sitemap for the Open GovTech Index site.

The site is one static page. When the API serves it, every page also has a real path such as
/theme/geospatial or /country/norway. Each path returns the same index.html with its own title,
description and link-preview tags, plus a flag that tells the script to route by path instead of
by hash. Hosting the static files elsewhere keeps working with #/ routes.

Feeds are how people get notified: the whole index, a theme, a country, an account, or any search
from the Repositories page (/feeds/repos.xml?country=...&tag=...). Theme, country and account pages
advertise their own feed, so pasting a page address into a feed reader finds it.
"""

import html
import os
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from .insights import Insights
from .queries import Catalogue

SITE_URL = os.getenv("GOVTECH_SITE_URL", "https://www.govtechindex.cloud").rstrip("/")
NAME = "Open GovTech Index"
DEFAULT_DESCRIPTION = (
    "Every public repository from government GitHub accounts, sorted into themes so you can see "
    "what is being built, what is growing, and who to learn from."
)
# Fixed pages: path -> (title, description).
STATIC_PAGES = {
    "/": (NAME, DEFAULT_DESCRIPTION),
    "/themes": (
        "Themes",
        "Every function of government in the index, with its tags, size and growth.",
    ),
    "/repos": (
        "Repositories",
        "Search public government repositories by text, country, theme and language.",
    ),
    "/countries": ("Countries", "Government open source by country and level of government."),
    "/week": (
        "This week",
        "New government repositories worth a look, themes on the move and star movers.",
    ),
    "/reuse": (
        "Reuse",
        "Which governments reuse other governments' code, and the most reused repositories.",
    ),
    "/compare": ("Compare", "Compare two countries or two themes side by side."),
    "/building-blocks": (
        "Building blocks",
        "Hand-picked reusable components for government services, with live figures.",
    ),
    "/practice": (
        "Open-source practice",
        "Licensing, descriptions and topics across government accounts, by country.",
    ),
    "/submit": ("Add an account", "Suggest a government GitHub account for the index."),
    "/about": (
        "About",
        "Why the Open GovTech Index exists, who runs it, and how every number is made.",
    ),
    "/method": (
        "About",
        "Why the Open GovTech Index exists, who runs it, and how every number is made.",
    ),
    "/data": ("Data and API", "A free, open REST API and MCP endpoint over the index."),
}
FEED_SIZE = 50
WEEK_FEED = ("/feeds/week.xml", "Open GovTech Index: new repositories")
# Filters a search feed accepts, in the order they appear in its title.
FEED_FILTERS = ("country", "domain", "tag", "kind", "language", "topic", "owner", "q")


def add_site_routes(app: FastAPI, catalogue: Catalogue, insights: Insights, site_dir: Path) -> None:
    template = (site_dir / "index.html").read_text()

    def shell(
        path: str,
        title: str,
        description: str,
        status: int = 200,
        feed: tuple[str, str] | None = None,
    ) -> HTMLResponse:
        full = NAME if title == NAME else f"{title} · {NAME}"
        url = SITE_URL + path
        t, d = html.escape(full), html.escape(description)
        head = (
            '<base href="/">\n'
            f'<link rel="canonical" href="{html.escape(url)}">\n'
            f'<meta property="og:title" content="{t}">\n'
            f'<meta property="og:description" content="{d}">\n'
            f'<meta property="og:url" content="{html.escape(url)}">\n'
            '<meta property="og:type" content="website">\n'
            f'<meta property="og:site_name" content="{NAME}">\n'
            f'<meta property="og:image" content="{SITE_URL}/og.png">\n'
            '<meta name="twitter:card" content="summary_large_image">\n'
            + "".join(
                f'<link rel="alternate" type="application/rss+xml" title="{html.escape(ft)}" '
                f'href="{html.escape(fp)}">\n'
                for fp, ft in ([feed] if feed else []) + [WEEK_FEED]
            )
            + "<script>window.OGI_PATHS = true;</script>\n"
        )
        page = template.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n' + head, 1)
        page = page.replace(f"<title>{NAME}</title>", f"<title>{t}</title>", 1)
        page = page.replace(
            f'<meta name="description" content="{DEFAULT_DESCRIPTION}">',
            f'<meta name="description" content="{d}">',
            1,
        )
        return HTMLResponse(page, status_code=status)

    def static_page(path: str):
        title, description = STATIC_PAGES[path]
        return lambda: shell(path, title, description)

    for path in STATIC_PAGES:
        app.add_api_route(path, static_page(path), methods=["GET"], include_in_schema=False)

    @app.get("/theme/{theme}", include_in_schema=False)
    def theme_page(theme: str):
        found = next((t for t in insights.themes()["themes"] if t["id"] == theme), None)
        if found is None:
            if not insights._known(theme):
                return shell(f"/theme/{theme}", "Not found", DEFAULT_DESCRIPTION, 404)
            found = insights._describe(theme)
            return shell(f"/theme/{theme}", found["name"], found["definition"])
        status = {"emerging": " Emerging.", "cooling": " Cooling."}.get(found.get("status"), "")
        desc = (
            f"{found['definition']} {found['repos']:,} government repositories in "
            f"{found['countries']} countries.{status}"
        )
        feed = (f"/feeds/theme/{theme}.xml", f"{NAME}: {found['name']}")
        if not catalogue.decision_model:
            feed = None
        return shell(f"/theme/{theme}", found["name"], desc, feed=feed)

    @app.get("/repo/{owner:path}/{name}", include_in_schema=False)
    def repo_page(owner: str, name: str):
        repo = catalogue.get_repo(owner, name)
        path = f"/repo/{owner}/{name}"
        if repo is None:
            return shell(path, "Not found", DEFAULT_DESCRIPTION, 404)
        desc = (repo["description"] or "A government repository").strip()
        return shell(path, repo["full_name"], f"{desc} Published by {owner}, {repo['country']}.")

    @app.get("/country/{slug}", include_in_schema=False)
    def country_page(slug: str):
        label = insights.label_for(slug)
        if label is None:
            return shell(f"/country/{slug}", "Not found", DEFAULT_DESCRIPTION, 404)
        c = next(x for x in insights.countries()["items"] if x["label"] == label)
        desc = (
            f"Government open source from {label}: {c['maintained']:,} maintained repositories "
            f"across {c['accounts']} accounts."
        )
        return shell(
            f"/country/{slug}", label, desc, feed=(f"/feeds/country/{slug}.xml", f"{NAME}: {label}")
        )

    @app.get("/account/{owner:path}", include_in_schema=False)
    def account_page(owner: str):
        found = catalogue.search_repos(owner=owner, limit=1)
        if not found["total"]:
            return shell(f"/account/{owner}", "Not found", DEFAULT_DESCRIPTION, 404)
        canonical = found["items"][0]["full_name"].rsplit("/", 1)[0]
        desc = f"{canonical}, {found['items'][0]['country']}: {found['total']:,} public repositories in the index."
        feed = (f"/feeds/account/{canonical}.xml", f"{NAME}: {canonical}")
        return shell(f"/account/{owner}", canonical, desc, feed=feed)

    # ------------------------------------------------------------------ feeds

    def feed(path: str, page: str, title: str, description: str, **filters) -> Response:
        """Up to FEED_SIZE notable original repositories, newest first. page is the site page
        the feed belongs to, used as the channel link."""
        try:
            items = catalogue.search_repos(
                include_forks=False,
                include_archived=False,
                notable=True,
                sort="newest",
                limit=FEED_SIZE,
                **filters,
            )["items"]
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        now = format_datetime(datetime.now(UTC))
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
            f"<title>{escape(title)}</title>",
            f"<link>{escape(SITE_URL + page)}</link>",
            f"<description>{escape(description)}</description>",
            f'<atom:link href="{escape(SITE_URL + path)}" rel="self" type="application/rss+xml"/>',
            f"<lastBuildDate>{now}</lastBuildDate>",
            "<ttl>720</ttl>",
        ]
        for r in items:
            created = r["created_at"]
            pub = ""
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    pub = f"<pubDate>{format_datetime(dt if dt.tzinfo else dt.replace(tzinfo=UTC))}</pubDate>"
                except ValueError:
                    pub = ""
            link = f"{SITE_URL}/repo/{r['full_name']}"
            body = f"{r['description'] or ''} ({r['country']}{', ' + r['language'] if r['language'] else ''})"
            parts.append(
                f"<item><title>{escape(r['full_name'])}</title><link>{escape(link)}</link>"
                f'<guid isPermaLink="false">{escape(r["html_url"])}</guid>{pub}'
                f"<description>{escape(body)}</description></item>"
            )
        parts.append("</channel></rss>")
        # The data changes at most daily, so readers and caches can hold a copy for an hour.
        return Response(
            "\n".join(parts),
            media_type="application/rss+xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    def theme_filter(theme: str) -> dict:
        if not insights._known(theme) or not catalogue.decision_model:
            raise HTTPException(status_code=404, detail="No such theme")
        t = insights._describe(theme)
        return {"domain" if t["type"] == "domain" else "tag": theme, "_name": t["name"]}

    @app.get("/feeds/week.xml", include_in_schema=False)
    def feed_week():
        return feed(
            "/feeds/week.xml",
            "/week",
            WEEK_FEED[1],
            "New government repositories worth a look.",
        )

    @app.get("/feeds/theme/{theme}.xml", include_in_schema=False)
    def feed_theme(theme: str):
        f = theme_filter(theme)
        name = f.pop("_name")
        return feed(
            f"/feeds/theme/{theme}.xml",
            f"/theme/{theme}",
            f"{NAME}: {name}",
            f"New government repositories in {name}.",
            **f,
        )

    @app.get("/feeds/country/{slug}.xml", include_in_schema=False)
    def feed_country(slug: str):
        label = insights.label_for(slug)
        if label is None:
            raise HTTPException(status_code=404, detail="No such registry label")
        return feed(
            f"/feeds/country/{slug}.xml",
            f"/country/{slug}",
            f"{NAME}: {label}",
            f"New government repositories from {label}.",
            country=label,
        )

    # Accounts on other forges are "{host}/{group}", so the owner may contain slashes.
    @app.get("/feeds/account/{owner:path}.xml", include_in_schema=False)
    def feed_account(owner: str):
        found = catalogue.search_repos(owner=owner, limit=1)
        if not found["total"]:
            raise HTTPException(status_code=404, detail="No such account")
        canonical = found["items"][0]["full_name"].rsplit("/", 1)[0]
        return feed(
            f"/feeds/account/{canonical}.xml",
            f"/account/{canonical}",
            f"{NAME}: {canonical}",
            f"New repositories from {canonical}.",
            owner=canonical,
        )

    @app.get("/feeds/repos.xml", include_in_schema=False)
    def feed_search(request: Request):
        """Any search from the Repositories page as a feed. Sort, forks, archived and
        maintained are ignored: a feed is always new, notable originals."""
        filters = {
            k: v.strip() for k in FEED_FILTERS if (v := request.query_params.get(k, "")).strip()
        }
        labels = []
        for k in FEED_FILTERS:
            if k not in filters:
                continue
            v = filters[k]
            if k in ("domain", "tag"):
                f = theme_filter(v)
                if f.get(k) != v:
                    raise HTTPException(status_code=404, detail=f"No such {k}")
                labels.append(f["_name"])
            elif k == "q":
                labels.append(f"“{v}”")
            else:
                labels.append(v)
        query = urlencode(filters)
        path = "/feeds/repos.xml" + (f"?{query}" if query else "")
        page = "/repos" + (f"?{query}&sort=newest" if query else "?sort=newest")
        title = f"{NAME}: {' · '.join(labels)}" if labels else WEEK_FEED[1]
        return feed(
            path,
            page,
            title,
            f"New government repositories matching {' · '.join(labels) or 'anything'}.",
            **filters,
        )

    # ------------------------------------------------------------------ badge

    @app.get("/badge/{owner:path}/{name}.svg", include_in_schema=False)
    def badge(owner: str, name: str):
        repo = catalogue.get_repo(owner, name)
        right = "listed"
        if repo and (repo.get("classification") or {}).get("domain_name"):
            right = repo["classification"]["domain_name"]
        if repo is None:
            right = "not listed"
        svg = _badge_svg("GovTech Index", right, "#2340b8" if repo else "#8a92ab")
        return Response(
            svg,
            media_type="image/svg+xml",
            status_code=200 if repo else 404,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # ------------------------------------------------------------------ crawlers

    @app.get("/robots.txt", include_in_schema=False)
    def robots():
        return PlainTextResponse(
            f"User-agent: *\nAllow: /\nDisallow: /mcp/\nSitemap: {SITE_URL}/sitemap.xml\n"
        )

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request):
        paths = [p for p in STATIC_PAGES if p != "/method"]
        paths += [f"/theme/{t['id']}" for t in insights.themes()["themes"]]
        paths += [f"/country/{c['slug']}" for c in insights.countries()["items"]]
        with catalogue._connect() as conn:
            owners = conn.execute(
                f"SELECT DISTINCT owner FROM repositories WHERE {catalogue.current_predicate()} ORDER BY owner"
            ).fetchall()
            repos = conn.execute(
                f"""SELECT owner, name FROM repositories WHERE {catalogue.current_predicate()}
                    AND fork = 0 AND archived = 0 ORDER BY stars DESC LIMIT 2000"""
            ).fetchall()
        paths += [f"/account/{o[0]}" for o in owners]
        paths += [f"/repo/{r[0]}/{r[1]}" for r in repos]
        body = "\n".join(f"<url><loc>{escape(SITE_URL + p)}</loc></url>" for p in paths)
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}\n</urlset>',
            media_type="application/xml",
        )


def _badge_svg(left: str, right: str, colour: str) -> str:
    """A flat, shields-style badge. Widths are estimated from character counts."""
    lw, rw = 12 + len(left) * 6.6, 12 + len(right) * 6.6
    w = lw + rw
    lt, rt = html.escape(left), html.escape(right)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="20" role="img" '
        f'aria-label="{lt}: {rt}"><title>{lt}: {rt}</title>'
        f'<rect width="{lw:.0f}" height="20" rx="3" fill="#141a2e"/>'
        f'<rect x="{lw - 3:.0f}" width="{rw + 3:.0f}" height="20" rx="3" fill="{colour}"/>'
        f'<rect x="{lw - 3:.0f}" width="3" height="20" fill="{colour}"/>'
        '<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{lw / 2:.1f}" y="14" text-anchor="middle">{lt}</text>'
        f'<text x="{lw + rw / 2:.1f}" y="14" text-anchor="middle">{rt}</text></g></svg>'
    )
