"""Regression tests for decision CLI failure and exhaustive-domain controls."""

from click.testing import CliRunner

from govtech_scraper.cli import main
from govtech_scraper.decision import load_taxonomy, score_repos
from tests.decision.test_decision import FakeClient


def test_cli_returns_failure_when_scoring_fails(tmp_db, sample_repos, monkeypatch):
    tmp_db.save_repositories(sample_repos)
    client = FakeClient(fail_urls={"uk-gov/notify"})
    monkeypatch.setattr("govtech_scraper.decision.cli.build_client", lambda *args: client)
    result = CliRunner().invoke(
        main, ["--db", tmp_db.db_path, "decide", "tag", "--limit", "1"]
    )
    assert "Scored: 0  failed: 1" in result.output
    assert result.exit_code != 0


async def test_all_domains_scores_every_taxonomy_tag(sample_repos):
    taxonomy = load_taxonomy()
    client = FakeClient()
    results = await score_repos(
        sample_repos[:1], client, taxonomy, max_domains=len(taxonomy.domains)
    )
    assert results[0].error is None
    assert set(client.calls[1][0]["questions"]) == {
        f"tag:{tag.id}" for tag in taxonomy.tags
    }
