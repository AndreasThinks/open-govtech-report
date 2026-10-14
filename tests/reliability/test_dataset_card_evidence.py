import json
from govtech_scraper.operations import dataset_card
from govtech_scraper.publication import prepare_publication


def test_dataset_card_distinguishes_evidence_from_legacy_metadata_tags(tmp_db, sample_repo, tmp_path):
    tmp_db.save_repositories([sample_repo])
    manifest = prepare_publication(tmp_db.db_path, tmp_path / 'bundle', 'a' * 40)
    manifest['classification'] = {'model': 'openai/gpt-6-luna', 'labelled_repos': 5,
        'current_total_repos': 8, 'human_certified': False}
    card = dataset_card(manifest)
    assert 'openai/gpt-6-luna' in card
    assert 'AI-assisted' in card
    assert 'not human-certified' in card
    assert 'metadata only' not in card
    assert 'SQLite' in card
