"""Tests for data models."""

from govtech_scraper.models import Repository, GovernmentAccount


def test_repository_defaults():
    repo = Repository(
        owner="test", name="repo",
        html_url="https://github.com/test/repo",
        country="US",
    )
    assert repo.stars == 0
    assert repo.forks == 0
    assert repo.topics == []
    assert repo.fork is False
    assert repo.archived is False
    assert repo.default_branch == "main"


def test_repository_with_all_fields(sample_repo):
    assert sample_repo.owner == "gov-org"
    assert sample_repo.language == "Python"
    assert sample_repo.stars == 150
    assert "open-data" in sample_repo.topics


def test_government_account_defaults():
    acct = GovernmentAccount(username="test", country="US")
    assert acct.account_type == "org"
