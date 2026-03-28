"""Shared test fixtures."""

import json
import pytest
import sqlite3
from pathlib import Path

from govtech_scraper.db import Database
from govtech_scraper.models import Repository, GovernmentAccount


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    yield db
    db.close()


@pytest.fixture
def sample_repo():
    """A sample Repository for testing."""
    return Repository(
        owner="gov-org",
        name="data-portal",
        html_url="https://github.com/gov-org/data-portal",
        country="United States",
        description="Open data portal for government datasets",
        language="Python",
        stars=150,
        forks=30,
        watchers=150,
        open_issues=5,
        size_kb=2048,
        license="MIT",
        topics=["open-data", "government", "api"],
        default_branch="main",
        fork=False,
        archived=False,
        created_at="2023-01-15T00:00:00Z",
        updated_at="2024-06-01T00:00:00Z",
        pushed_at="2024-06-01T00:00:00Z",
        commit_count=500,
    )


@pytest.fixture
def sample_repos():
    """Multiple sample repos for batch testing."""
    return [
        Repository(
            owner="uk-gov", name="notify",
            html_url="https://github.com/uk-gov/notify",
            country="United Kingdom",
            description="GOV.UK Notify - sending emails and SMS",
            language="Python", stars=500, forks=80, watchers=500,
            open_issues=10, size_kb=5000, license="MIT",
            topics=["notifications", "email", "sms"],
        ),
        Repository(
            owner="us-gsa", name="sam-api",
            html_url="https://github.com/us-gsa/sam-api",
            country="United States",
            description="System for Award Management API",
            language="JavaScript", stars=200, forks=50, watchers=200,
            open_issues=3, size_kb=1500, license="Apache-2.0",
            topics=["procurement", "api"],
        ),
        Repository(
            owner="france-data", name="cadastre",
            html_url="https://github.com/france-data/cadastre",
            country="France",
            description="French cadastral data processing",
            language="Python", stars=80, forks=20, watchers=80,
            open_issues=1, size_kb=800, license="AGPL-3.0",
            topics=["geospatial", "cadastre"],
        ),
    ]


@pytest.fixture
def sample_account():
    """A sample GovernmentAccount."""
    return GovernmentAccount(
        username="gov-org",
        country="United States",
        account_type="org",
    )
