from govtech_scraper.api.insights import Insights
from govtech_scraper.api.queries import Catalogue
from govtech_scraper.decision.evidence import EvidenceStore, source_from_repo
from govtech_scraper.decision.taxonomy import load_taxonomy


def _save_evidence(db, repo):
    taxonomy = load_taxonomy()
    store = EvidenceStore(db)
    source = source_from_repo(
        repo,
        readme="GOV.UK Notify - sending emails and SMS",
        readme_blob_sha="readme-sha",
        readme_status="fetched",
    )
    store.save_success(
        html_url=repo.html_url,
        taxonomy_version=taxonomy.version,
        model="anthropic/claude-haiku-5.5",
        prompt_version="evidence-v1",
        input_hash=source.input_hash("anthropic/claude-haiku-5.5", "evidence-v1", taxonomy),
        source=source,
        domain="00",
        kind="api-service",
        tags=[
            {
                "id": "notifications",
                "quote": "sending emails and SMS",
                "rationale": "Notification delivery is the central purpose.",
                "source_field": "readme",
            }
        ],
        usage={"prompt_tokens": 100, "completion_tokens": 30, "total_cost": 0.0002},
    )


def test_evidence_repo_detail_uses_null_probability_and_exposes_provenance(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    _save_evidence(tmp_db, sample_repos[0])
    catalogue = Catalogue(tmp_db.db_path)

    repo = catalogue.get_repo("uk-gov", "notify")
    assert repo["classification"]["model"] == "anthropic/claude-haiku-5.5"
    assert repo["classification"]["confidence_mode"] == "evidence"
    assert repo["classification"]["domain_probability"] is None
    assert repo["classification"]["tags"] == [
        {
            "id": "notifications",
            "probability": None,
            "quote": "sending emails and SMS",
            "rationale": "Notification delivery is the central purpose.",
            "source_field": "readme",
        }
    ]


def test_evidence_filters_counts_and_coverage_are_live_not_sample(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    _save_evidence(tmp_db, sample_repos[0])
    catalogue = Catalogue(tmp_db.db_path)

    by_tag = catalogue.search_repos(tag="notifications")
    assert by_tag["total"] == 1
    taxonomy = catalogue.taxonomy_summary()
    assert taxonomy["classification_model"] == "anthropic/claude-haiku-5.5"
    assert taxonomy["confidence_mode"] == "evidence"
    assert next(d for d in taxonomy["domains"] if d["id"] == "00")["repos"] == 1
    overview = Insights(catalogue).overview()
    assert overview["classification"]["assessed_repos"] == 1
    assert overview["classification"]["labelled_repos"] == 1
    assert overview["classification"]["unclassified_repos"] == 2
    assert overview["classification"]["failed_repos"] == 0


def test_default_model_prefers_fresh_results_over_stale_and_failed(tmp_db, sample_repos):
    from dataclasses import replace
    tmp_db.save_repositories(sample_repos[:2])
    for repo in sample_repos[:2]:
        _save_evidence(tmp_db, repo)
    tmp_db.conn.execute("UPDATE evidence_classifications SET model='stale'")
    tmp_db.conn.execute("UPDATE evidence_classifications SET status='failed' WHERE html_url=?",
                        (sample_repos[1].html_url,))
    changed = replace(sample_repos[0], description='Changed purpose')
    tmp_db.conn.execute('UPDATE repositories SET description=? WHERE html_url=?',
                        (changed.description, changed.html_url))
    tmp_db.conn.commit()
    _save_evidence(tmp_db, changed)
    catalogue = Catalogue(tmp_db.db_path)
    assert catalogue.decision_model == 'anthropic/claude-haiku-5.5'
    assert catalogue.get_repo(changed.owner, changed.name)['classification'] is not None


def test_evidence_detail_exposes_field_abstentions(tmp_db, sample_repos):
    import json
    tmp_db.save_repositories(sample_repos)
    _save_evidence(tmp_db, sample_repos[0])
    reasons = {'kind': 'Source does not establish software kind', 'tags': ['Uncertain additional tags']}
    tmp_db.conn.execute('UPDATE evidence_classifications SET abstentions_json=?', (json.dumps(reasons),))
    tmp_db.conn.commit()
    detail = Catalogue(tmp_db.db_path).get_repo('uk-gov', 'notify')
    assert detail['classification']['abstentions'] == reasons


def test_evidence_metadata_change_makes_classification_stale(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    _save_evidence(tmp_db, sample_repos[0])
    tmp_db.conn.execute(
        "UPDATE repositories SET description=? WHERE html_url=?",
        ("A changed purpose", sample_repos[0].html_url),
    )
    tmp_db.conn.commit()

    catalogue = Catalogue(tmp_db.db_path)
    assert catalogue.search_repos(tag="notifications")["total"] == 0
    assert catalogue.get_repo("uk-gov", "notify")["classification"] is None
