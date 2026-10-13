"""Tests for the public REST API and MCP tools, against a small classified database."""

import json

import pytest

pytest.importorskip("fastapi", reason="needs the api extra: uv sync --extra api")
pytest.importorskip("mcp", reason="needs the api extra: uv sync --extra api")

from starlette.testclient import TestClient

from govtech_scraper.api.app import create_app
from govtech_scraper.api.queries import Catalogue
from govtech_scraper.decision import load_taxonomy, run_decision_tagging
from tests.decision.test_decision import FakeClient

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
async def classified_db(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    tmp_db.save_repo_tag("https://github.com/uk-gov/notify", "messaging", 0.9, source="llm")
    await run_decision_tagging(tmp_db, FakeClient(), load_taxonomy())
    return tmp_db.db_path


@pytest.fixture
def client(classified_db):
    with TestClient(create_app(Catalogue(classified_db)), base_url="https://govtech.example") as c:
        yield c


def test_search_filters_and_pages(client):
    body = client.get("/v1/repos", params={"limit": 2}).json()
    assert body["total"] == 3 and len(body["items"]) == 2
    assert body["items"][0]["full_name"] == "uk-gov/notify"  # most stars first

    by_country = client.get("/v1/repos", params={"country": "france"}).json()
    assert [r["full_name"] for r in by_country["items"]] == ["france-data/cadastre"]

    by_topic = client.get("/v1/repos", params={"topic": "procurement"}).json()
    assert [r["full_name"] for r in by_topic["items"]] == ["us-gsa/sam-api"]


def test_search_by_classification(client):
    by_domain = client.get("/v1/repos", params={"domain": "00"}).json()
    assert [r["full_name"] for r in by_domain["items"]] == ["uk-gov/notify"]
    assert by_domain["items"][0]["domain"] == "00"

    by_tag = client.get("/v1/repos", params={"tag": "notifications"}).json()
    assert by_tag["total"] == 1


def test_repo_detail_includes_both_tag_sources(client):
    repo = client.get("/v1/repos/UK-GOV/notify").json()
    assert repo["llm_tags"] == ["messaging"]
    assert repo["classification"]["domain_name"] == "Cross-government capabilities"
    assert repo["classification"]["tags"] == [{"id": "notifications", "probability": 0.9}]
    assert client.get("/v1/repos/nobody/nothing").status_code == 404


def test_bad_sort_is_a_client_error(client):
    assert client.get("/v1/repos", params={"sort": "random"}).status_code == 400


def test_countries_taxonomy_and_stats(client):
    countries = {c["country"]: c["repos"] for c in client.get("/v1/countries").json()}
    assert countries == {"United Kingdom": 1, "United States": 1, "France": 1}
    taxonomy = client.get("/v1/taxonomy").json()
    assert taxonomy["classification_model"] == "fake-kev"
    assert next(d for d in taxonomy["domains"] if d["id"] == "07")["repos"] == 2
    stats = client.get("/v1/stats").json()
    assert stats["repos"] == 3 and stats["classified_repos"] == 3


def test_catalogue_without_classification(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    catalogue = Catalogue(tmp_db.db_path)
    assert catalogue.decision_model is None
    assert catalogue.search_repos()["total"] == 3
    with pytest.raises(ValueError):
        catalogue.search_repos(domain="07")


def _rpc(client, method, params=None, id_=1):
    response = client.post(
        "/mcp/",
        headers=MCP_HEADERS,
        json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_mcp_lists_and_calls_tools(client):
    init = _rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    )
    assert init["result"]["serverInfo"]["name"] == "govtech-repos"

    tools = {t["name"] for t in _rpc(client, "tools/list", id_=2)["result"]["tools"]}
    assert tools == {
        "search_repositories",
        "get_repository",
        "list_countries",
        "get_taxonomy",
        "get_statistics",
        "list_themes",
        "get_theme",
    }

    result = _rpc(
        client,
        "tools/call",
        {
            "name": "search_repositories",
            "arguments": {"domain": "07", "limit": 5},
        },
        id_=3,
    )["result"]
    payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert payload["total"] == 2


def test_overview(client):
    body = client.get("/v1/overview").json()
    assert body["totals"]["repos"] == 3 and body["totals"]["original"] == 3
    assert len(body["timeline"]) == 36
    assert body["classification"]["model"] == "fake-kev"
    assert body["classification"]["classified_original_repos"] == 3
    assert {c["country"] for c in body["countries"]} == {"United Kingdom", "United States", "France"}


def test_themes_list_and_detail(client):
    themes = {t["id"]: t for t in client.get("/v1/themes").json()["themes"]}
    assert themes["07"]["type"] == "domain" and themes["07"]["repos"] == 2
    assert themes["notifications"]["type"] == "tag" and themes["notifications"]["repos"] == 1
    assert themes["notifications"]["name"] == "Notifications"

    health = client.get("/v1/themes/07").json()
    assert health["name"] == "Health" and health["repos"] == 2
    assert len(health["monthly_new"]) == 24
    assert {r["full_name"] for r in health["top_repos"]} == {"us-gsa/sam-api", "france-data/cadastre"}
    assert client.get("/v1/themes/not-a-theme").status_code == 404


def test_repo_context(client):
    body = client.get("/v1/repos/uk-gov/notify/context").json()
    assert body["full_name"] == "uk-gov/notify"
    assert body["history"] and body["reused_by"] == []
    assert client.get("/v1/repos/nobody/nothing/context").status_code == 404


def test_themes_empty_without_classification(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    with TestClient(create_app(Catalogue(tmp_db.db_path))) as c:
        themes = c.get("/v1/themes").json()
        assert themes["model"] is None and themes["themes"] == []
        assert c.get("/v1/themes/07").json()["repos"] == 0
        assert c.get("/v1/overview").json()["classification"]["model"] is None


def test_site_is_served_at_root(client):
    page = client.get("/")
    assert page.status_code == 200 and "Open GovTech Index" in page.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api").json()["rest"] == "/v1/repos"


def test_tag_names_are_readable():
    from govtech_scraper.api.insights import tag_name

    assert tag_name("ai-and-machine-learning") == "AI and machine learning"
    assert tag_name("transparency-and-foi") == "Transparency and FOI"
