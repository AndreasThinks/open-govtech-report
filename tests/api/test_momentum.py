"""Pace against the index, breadth and status for themes."""

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi", reason="needs the api extra: uv sync --extra api")

from starlette.testclient import TestClient

from govtech_scraper.api.app import create_app
from govtech_scraper.api.insights import pace_stats, pace_status
from govtech_scraper.api.queries import Catalogue
from govtech_scraper.decision import load_taxonomy, run_decision_tagging
from govtech_scraper.models import Repository
from tests.decision.test_decision import FakeClient


def _ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo(owner: str, name: str, days: float) -> Repository:
    return Repository(
        owner=owner,
        name=name,
        country="United Kingdom",
        html_url=f"https://github.com/{owner}/{name}",
        description=f"{name} service",
        language="Python",
        stars=1,
        forks=0,
        watchers=0,
        open_issues=0,
        size_kb=100,
        license="MIT",
        topics=[],
        created_at=_ago(days),
        updated_at=_ago(days),
        pushed_at=_ago(days),
    )


def test_pace_is_relative_to_the_index():
    same = pace_stats(30, 15, 2.0)
    assert same["pace"] == pytest.approx(1.0, abs=0.02)
    assert same["pace_low"] < 1 < same["pace_high"]
    assert pace_stats(5, 5, None)["pace"] is None


def test_small_counts_give_wide_intervals():
    wide, narrow = pace_stats(6, 3, 1.0), pace_stats(600, 300, 1.0)
    assert wide["pace_high"] / wide["pace_low"] > narrow["pace_high"] / narrow["pace_low"]
    assert pace_stats(0, 0, 1.0)["pace"] == 1.0


def test_status_needs_confidence_and_breadth():
    rising = pace_stats(80, 30, 1.1)
    assert pace_status(80, 30, rising, accounts=12, top_share=20) == "emerging"
    assert pace_status(80, 30, rising, accounts=12, top_share=72) == "concentrated"
    assert pace_status(80, 30, rising, accounts=3, top_share=40) == "concentrated"
    assert pace_status(30, 80, pace_stats(30, 80, 1.1), accounts=12, top_share=20) == "cooling"
    assert pace_status(40, 38, pace_stats(40, 38, 1.1), accounts=12, top_share=20) == "steady"
    assert pace_status(8, 2, pace_stats(8, 2, 1.1), accounts=8, top_share=20) == "too_small"


@pytest.fixture
async def momentum_client(tmp_db):
    # Notifications: 30 new repositories from 10 accounts this year, 5 the year before.
    # Everything else (Health): 20 each year, so the index doubles and Health falls behind it.
    repos = [_repo(f"team{i % 10}", f"notify-{i}", 60 + i * 5) for i in range(30)]
    repos += [_repo("team0", f"notify-old-{i}", 420 + i * 20) for i in range(5)]
    repos += [_repo("clinic", f"records-{i}", 60 + i * 10) for i in range(20)]
    repos += [_repo("clinic", f"records-old-{i}", 420 + i * 12) for i in range(20)]
    tmp_db.save_repositories(repos)
    await run_decision_tagging(tmp_db, FakeClient(), load_taxonomy())
    with TestClient(create_app(Catalogue(tmp_db.db_path))) as c:
        yield c


def test_themes_carry_pace_breadth_and_trend(momentum_client):
    body = momentum_client.get("/v1/themes").json()
    assert body["index_pace"] == pytest.approx(2.0)
    assert len(body["trend_months"]) == 24
    themes = {t["id"]: t for t in body["themes"]}
    notify = themes["notifications"]
    assert (notify["new_window"], notify["new_prior"]) == (30, 5)
    assert notify["accounts_window"] == 10 and notify["top_account_share"] == 10
    assert notify["status"] == "emerging" and notify["pace_low"] > 1
    assert len(notify["trend"]) == 24 and sum(notify["trend"]) == 35
    assert themes["07"]["status"] == "cooling"

    detail = momentum_client.get("/v1/themes/notifications").json()
    assert detail["status"] == "emerging" and detail["pace"] == notify["pace"]
    assert momentum_client.get("/v1/week").json()["fastest_themes"][0]["id"] == "notifications"
