"""Countries, accounts, reuse, this week, practice, building blocks, page paths, feeds and badges."""

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi", reason="needs the api extra: uv sync --extra api")

from starlette.testclient import TestClient

from govtech_scraper.api.app import create_app
from govtech_scraper.api.places import place, slug
from govtech_scraper.api.queries import Catalogue
from govtech_scraper.decision import load_taxonomy, run_decision_tagging
from govtech_scraper.models import Repository
from tests.decision.test_decision import FakeClient


def _ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo(owner, name, country, **kw) -> Repository:
    base = dict(
        html_url=f"https://github.com/{owner}/{name}",
        description=f"{name} service",
        language="Python",
        stars=10,
        forks=0,
        watchers=0,
        open_issues=0,
        size_kb=100,
        license="MIT",
        topics=["government"],
        created_at=_ago(400),
        updated_at=_ago(10),
        pushed_at=_ago(10),
    )
    base.update(kw)
    return Repository(owner=owner, name=name, country=country, **base)


@pytest.fixture
async def site_db(tmp_db):
    tmp_db.save_repositories(
        [
            _repo("alphagov", "notify-api", "U.K. Central", stars=900),
            _repo(
                "alphagov", "old-thing", "U.K. Central", archived=True, updated_at=_ago(2), stars=50
            ),
            _repo(
                "alphagov",
                "placeholder",
                "U.K. Central",
                description=None,
                created_at=_ago(2),
                pushed_at=_ago(2),
            ),
            _repo(
                "alphagov", "fresh-notify", "U.K. Central", created_at=_ago(3), pushed_at=_ago(1)
            ),
            _repo("govau", "notify-api", "Australia", fork=True, fork_source="alphagov/notify-api"),
            _repo(
                "nzgov", "notify-api", "New Zealand", fork=True, fork_source="alphagov/notify-api"
            ),
            _repo(
                "leeds", "notify-api", "U.K. Councils", fork=True, fork_source="alphagov/notify-api"
            ),
            _repo("navikt", "health-forms", "Norway", license=None, topics=[]),
        ]
    )
    await run_decision_tagging(tmp_db, FakeClient(), load_taxonomy())
    return tmp_db.db_path


@pytest.fixture
def client(site_db):
    with TestClient(create_app(Catalogue(site_db))) as c:
        yield c


def test_places_split_labels():
    assert place("U.K. Councils") == {
        "label": "U.K. Councils",
        "slug": "uk-councils",
        "country": "United Kingdom",
        "level": "local",
    }
    assert place("Norway")["level"] == "national"
    assert place("United Nations")["level"] == "international"
    assert slug("U.S. Military and Intelligence") == "us-military-and-intelligence"


def test_search_filters_owner_maintained_notable(client):
    owned = client.get("/v1/repos", params={"owner": "ALPHAGOV"}).json()
    assert owned["total"] == 4
    maintained = client.get("/v1/repos", params={"owner": "alphagov", "maintained": True}).json()
    assert "alphagov/old-thing" not in {r["full_name"] for r in maintained["items"]}
    notable = client.get("/v1/repos", params={"owner": "alphagov", "notable": True}).json()
    names = {r["full_name"] for r in notable["items"]}
    assert "alphagov/placeholder" not in names and "alphagov/fresh-notify" in names


def test_overview_newest_hides_placeholders(client):
    newest = [r["full_name"] for r in client.get("/v1/overview").json()["newest"]]
    assert "alphagov/placeholder" not in newest and newest[0] == "alphagov/fresh-notify"


def test_countries_and_country_profile(client):
    labels = {c["label"]: c for c in client.get("/v1/places").json()["items"]}
    assert labels["U.K. Councils"]["level"] == "local"
    assert client.get("/v1/countries").json()[0]["slug"]
    uk = client.get("/v1/countries/uk-central").json()
    assert uk["label"] == "U.K. Central" and uk["country"] == "United Kingdom"
    assert {r["label"] for r in uk["reused_by"]} == {"Australia", "New Zealand", "U.K. Councils"}
    assert uk["most_reused"][0]["full_name"] == "alphagov/notify-api"
    assert uk["theme_mix"]["labelled"] >= 3
    assert len(uk["monthly_new"]) == 24
    assert client.get("/v1/countries/atlantis").status_code == 404


def test_account(client):
    body = client.get("/v1/accounts/AlphaGov").json()
    assert body["owner"] == "alphagov" and body["original"] == 4
    assert body["most_reused"][0]["reused_by_accounts"] == 3
    assert client.get("/v1/accounts/nobody").status_code == 404


def test_reuse(client):
    body = client.get("/v1/reuse").json()
    assert body["forks"] == 3 and body["cross_border_forks"] == 3
    assert body["most_reused"][0] == {
        "full_name": "alphagov/notify-api",
        "country": "U.K. Central",
        "reused_by_accounts": 3,
        "reused_in_labels": 3,
    }


def test_week(client):
    body = client.get("/v1/week").json()
    assert body["new"] == 2 and body["new_notable"] == 1
    assert [r["full_name"] for r in body["notable"]] == ["alphagov/fresh-notify"]
    assert body["archived"]["count"] == 1


def test_practice_and_building_blocks(client):
    practice = client.get("/v1/practice").json()
    assert practice["index"]["maintained"] == 4
    assert practice["index"]["licensed_share"] == 75.0
    blocks = client.get("/v1/building-blocks").json()
    assert blocks["groups"] and all(g["picks"] == [] for g in blocks["groups"])


def test_theme_teams(client):
    body = client.get("/v1/themes/07").json()
    assert body["teams"] and {"owner", "maintained", "repos"} <= set(body["teams"][0])


def test_page_paths_have_their_own_titles(client):
    page = client.get("/theme/notifications")
    assert page.status_code == 200
    assert "<title>Notifications · Open GovTech Index</title>" in page.text
    assert "window.OGI_PATHS = true" in page.text and '<base href="/">' in page.text
    assert "U.K. Central · Open GovTech Index" in client.get("/country/uk-central").text
    assert "alphagov/notify-api" in client.get("/repo/alphagov/notify-api").text
    assert client.get("/repo/nobody/nothing").status_code == 404
    assert client.get("/about").status_code == 200
    assert client.get("/").text.count("OGI_PATHS") == 1
    assert client.get("/app.js").status_code == 200


def test_feeds_badge_and_sitemap(client):
    feed = client.get("/feeds/week.xml")
    assert feed.headers["content-type"].startswith("application/rss+xml")
    assert "alphagov/fresh-notify" in feed.text and "placeholder" not in feed.text
    assert client.get("/feeds/country/uk-central.xml").status_code == 200
    assert client.get("/feeds/theme/notifications.xml").status_code == 200
    assert client.get("/feeds/theme/nope.xml").status_code == 404
    badge = client.get("/badge/alphagov/notify-api.svg")
    assert badge.status_code == 200 and badge.text.startswith("<svg")
    assert client.get("/badge/nobody/nothing.svg").status_code == 404
    sitemap = client.get("/sitemap.xml").text
    assert "/country/uk-central</loc>" in sitemap and "/account/alphagov</loc>" in sitemap
    assert "Sitemap:" in client.get("/robots.txt").text
