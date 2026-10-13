"""Endpoint regressions for published legacy schemas and retired repositories."""
import hashlib
import json
import sqlite3

import pytest
from starlette.testclient import TestClient

from govtech_scraper.api.app import create_app
from govtech_scraper.api.queries import Catalogue
from tests.api.test_api import classified_db, _rpc  # noqa: F401


@pytest.fixture(params=["legacy", "current"])
def published_db(classified_db, tmp_path, request):
    path = tmp_path / "published.db"
    with sqlite3.connect(classified_db) as source, sqlite3.connect(path) as copy:
        source.backup(copy)
        if request.param == "legacy":
            copy.execute("ALTER TABLE repositories DROP COLUMN current")
            copy.execute("ALTER TABLE tags DROP COLUMN kind")
        else:
            copy.execute("UPDATE repositories SET current=0 WHERE owner='us-gsa'")
            copy.execute("UPDATE repositories SET current=NULL WHERE owner='uk-gov'")
    copy.close()
    source.close()
    return path, 3 if request.param == "legacy" else 2


REST_PATHS = ["/v1/overview", "/v1/themes", "/v1/themes/07", "/v1/themes/notifications",
              "/v1/repos/uk-gov/notify/context", "/v1/repos/uk-gov/notify", "/v1/repos",
              "/v1/stats", "/v1/countries", "/v1/taxonomy"]


@pytest.mark.parametrize("path", REST_PATHS)
def test_published_rest(published_db, path):
    db, count = published_db
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with TestClient(create_app(Catalogue(str(db))), raise_server_exceptions=False) as client:
        response = client.get(path)
        assert response.status_code == 200, response.text
        body = response.json()
        if path == "/v1/overview":
            assert body["totals"]["repos"] == count
            assert body["classification"]["classified_original_repos"] == count
        elif path == "/v1/stats":
            assert body["repos"] == body["classified_repos"] == count
        elif path == "/v1/repos":
            assert body["total"] == count
        elif path == "/v1/countries":
            assert sum(c["repos"] for c in body) == count
        elif path == "/v1/themes":
            assert next(t for t in body["themes"] if t["id"] == "07")["repos"] == count - 1
        elif path == "/v1/themes/07":
            assert body["repos"] == len(body["top_repos"]) == count - 1
        elif path == "/v1/taxonomy":
            assert next(d for d in body["domains"] if d["id"] == "07")["repos"] == count - 1
        elif path.endswith("/context"):
            assert body["history"]
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


MCP_CASES = [("search_repositories", {}, "total"), ("get_repository", {"owner": "uk-gov", "name": "notify"}, None),
             ("list_countries", {}, None), ("get_taxonomy", {}, None),
             ("get_statistics", {}, "classified_repos"), ("list_themes", {}, None),
             ("get_theme", {"theme": "07"}, "repos")]


@pytest.mark.parametrize("name,args,key", MCP_CASES)
def test_published_mcp(published_db, name, args, key):
    db, count = published_db
    before = db.read_bytes()
    with TestClient(create_app(Catalogue(str(db)))) as client:
        result = _rpc(client, "tools/call", {"name": name, "arguments": args})["result"]
        assert not result.get("isError"), result
        payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        if key:
            assert payload[key] == (count - 1 if name == "get_theme" else count)
    assert db.read_bytes() == before


def test_retired_details_and_context_are_absent(classified_db):
    with sqlite3.connect(classified_db) as conn:
        conn.execute("UPDATE repositories SET current=0 WHERE owner='uk-gov'")
        conn.execute("UPDATE repositories SET fork=1, fork_source='france-data/cadastre' WHERE owner='uk-gov'")
    with TestClient(create_app(Catalogue(classified_db))) as client:
        assert client.get("/v1/repos/uk-gov/notify").status_code == 404
        assert client.get("/v1/repos/uk-gov/notify/context").status_code == 404
        assert client.get("/v1/repos/france-data/cadastre/context").json()["reused_by"] == []
        result = _rpc(client, "tools/call", {"name": "get_repository", "arguments": {"owner": "uk-gov", "name": "notify"}})["result"]
        assert result["isError"]
