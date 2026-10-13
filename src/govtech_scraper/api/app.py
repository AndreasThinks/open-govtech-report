"""Public, read-only REST API and MCP endpoint over the published database.

    GOVTECH_DB=govtech.db uv run --extra api uvicorn govtech_scraper.api.app:app

REST endpoints live under /v1 (OpenAPI docs at /docs); the MCP server is at /mcp; the
Open GovTech Index site is served from / when its files are installed.
"""

import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from .insights import Insights
from .mcp_tools import build_mcp
from .queries import MAX_LIMIT, SORTS, Catalogue

SITE_DIR = Path(__file__).resolve().parent.parent / "site"


def create_app(catalogue: Catalogue | None = None, site_dir: Path | None = SITE_DIR) -> FastAPI:
    catalogue = catalogue or Catalogue()
    insights = Insights(catalogue)
    mcp = build_mcp(catalogue, insights)
    # Stateless JSON responses: no sessions to keep on a small free host, and any client can call it.
    # DNS-rebinding protection guards local servers; this one is public by design.
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Government GitHub repositories API",
        version="1",
        description=(
            "Read-only access to public repositories owned by accounts in the government.github.com "
            "registry, classified against the UN COFOG functions of government. "
            "MCP clients can connect to /mcp."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"]
    )

    @app.get("/v1/repos", summary="Search repositories")
    def search_repos(
        q: str | None = Query(None, description="Text in owner, name, description or topics"),
        country: str | None = None,
        language: str | None = None,
        topic: str | None = None,
        domain: str | None = Query(None, description="COFOG domain id, e.g. 07"),
        tag: str | None = Query(None, description="Taxonomy tag id"),
        kind: str | None = Query(None, description="Kind id, e.g. library"),
        min_stars: int = Query(0, ge=0),
        include_forks: bool = True,
        include_archived: bool = True,
        sort: str = Query("stars", enum=list(SORTS)),
        limit: int = Query(20, ge=1, le=MAX_LIMIT),
        offset: int = Query(0, ge=0),
    ):
        try:
            return catalogue.search_repos(
                q=q,
                country=country,
                language=language,
                topic=topic,
                domain=domain,
                tag=tag,
                kind=kind,
                min_stars=min_stars,
                include_forks=include_forks,
                include_archived=include_archived,
                sort=sort,
                limit=limit,
                offset=offset,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.get("/v1/repos/{owner}/{name}", summary="One repository with tags and classification")
    def get_repo(owner: str, name: str):
        repo = catalogue.get_repo(owner, name)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not in the catalogue")
        return repo

    @app.get("/v1/repos/{owner}/{name}/context", summary="History, government forks and similar repos")
    def repo_context(owner: str, name: str):
        context = insights.repo_context(owner, name)
        if context is None:
            raise HTTPException(status_code=404, detail="Repository not in the catalogue")
        return context

    @app.get("/v1/overview", summary="Headline numbers, timeline, countries and newest repos")
    def overview():
        return insights.overview()

    @app.get("/v1/themes", summary="Domains and tags with size, growth and spread")
    def themes():
        return insights.themes()

    @app.get("/v1/themes/{theme}", summary="One domain or tag in detail")
    def theme(theme: str):
        detail = insights.theme(theme)
        if detail is None:
            raise HTTPException(status_code=404, detail="Not a domain or tag in the taxonomy")
        return detail

    @app.get("/v1/countries", summary="Countries with repository counts")
    def countries():
        return catalogue.list_countries()

    @app.get("/v1/taxonomy", summary="COFOG domains, tags and kinds with counts")
    def taxonomy():
        return catalogue.taxonomy_summary()

    @app.get("/v1/stats", summary="Headline numbers")
    def stats():
        return catalogue.stats()

    @app.get("/api", include_in_schema=False)
    def api_index():
        return {"docs": "/docs", "openapi": "/openapi.json", "mcp": "/mcp/", "rest": "/v1/repos"}

    app.mount("/mcp", mcp_app)
    if site_dir and (site_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=site_dir, html=True), name="site")
    else:

        @app.get("/", include_in_schema=False)
        def root():
            return api_index()

    return app


def _lazy_app() -> FastAPI:
    return create_app(Catalogue(os.getenv("GOVTECH_DB", "govtech.db")))


def __getattr__(name: str):
    # `uvicorn govtech_scraper.api.app:app` builds the app on first access, so importing this
    # module (in tests, for create_app) does not need a database on disk.
    if name == "app":
        return _lazy_app()
    raise AttributeError(name)
