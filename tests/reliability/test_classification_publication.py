import json
import sqlite3
import pytest
from govtech_scraper import publication
from govtech_scraper.decision.evidence import EvidenceStore, source_from_repo, taxonomy_digest
from govtech_scraper.decision.taxonomy import load_taxonomy


def _prepare():
    function=getattr(publication,'prepare_classification_publication',None)
    assert callable(function), 'Explicit evidence-classification publication gate is missing'
    return function


def _label(db,repo):
    taxonomy=load_taxonomy()
    source=source_from_repo(repo)
    EvidenceStore(db).save_success(html_url=repo.html_url,taxonomy_version=taxonomy.version,
        model='anthropic/claude-haiku-5.5',prompt_version='evidence-v1',
        input_hash=source.input_hash('anthropic/claude-haiku-5.5','evidence-v1',taxonomy),
        source=source,domain='00',kind='library',tags=[],usage={'cost':0.0001},
        abstentions={},taxonomy_sha=taxonomy_digest(taxonomy))


def test_partial_evidence_publication_requires_opt_in(tmp_db,sample_repos,tmp_path):
    prepare=_prepare()
    tmp_db.save_repositories(sample_repos[:2])
    _label(tmp_db,sample_repos[0])
    with pytest.raises(ValueError,match='partial'):
        prepare(tmp_db.db_path,tmp_path/'blocked','a'*40)


def test_partial_evidence_publication_records_snapshot_coverage(tmp_db,sample_repos,tmp_path):
    prepare=_prepare()
    tmp_db.save_repositories(sample_repos[:2])
    _label(tmp_db,sample_repos[0])
    snapshots=tmp_db.conn.execute('SELECT COUNT(*) FROM repository_snapshots').fetchone()[0]
    directory=tmp_path/'allowed'
    manifest=prepare(tmp_db.db_path,directory,'a'*40,allow_partial=True)
    assert manifest['classification']['confidence_mode']=='evidence'
    assert manifest['classification']['assessed_repos']==1
    assert manifest['classification']['partial'] is True
    assert manifest['classification']['human_certified'] is False
    assert json.loads((directory/'manifest.json').read_text())==manifest
    publication.validate_bundle(directory)
    with sqlite3.connect(directory/'govtech.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM repository_snapshots').fetchone()[0]==snapshots
        assert conn.execute('SELECT COUNT(*) FROM evidence_classifications').fetchone()[0]==1


def test_public_bundle_excludes_private_operator_state(tmp_db, sample_repo, tmp_path):
    tmp_db.save_repositories([sample_repo])
    _label(tmp_db, sample_repo)
    private_tables = ('openrouter_budget_ledger', 'openrouter_response_receipts',
                      'openrouter_future_operator_state', 'worker_status')
    for name in private_tables:
        tmp_db.conn.execute(f'CREATE TABLE IF NOT EXISTS "{name}" (private_value TEXT)')
    tmp_db.conn.execute('INSERT INTO openrouter_future_operator_state VALUES (?)',
                        ('PRIVATE_OPERATOR_MARKER_DO_NOT_PUBLISH',))
    tmp_db.conn.commit()
    directory = tmp_path / 'public'
    _prepare()(tmp_db.db_path, directory, 'a'*40, allow_partial=True)
    with sqlite3.connect(directory / 'govtech.db') as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not tables.intersection(private_tables)
        assert conn.execute('SELECT COUNT(*) FROM evidence_classifications').fetchone()[0] == 1
    assert b'PRIVATE_OPERATOR_MARKER_DO_NOT_PUBLISH' not in (directory / 'govtech.db').read_bytes()
    assert tmp_db.conn.execute('SELECT private_value FROM openrouter_future_operator_state').fetchone()[0] == 'PRIVATE_OPERATOR_MARKER_DO_NOT_PUBLISH'


def test_publication_review_withholds_tags_without_changing_private_results(tmp_db, sample_repo, tmp_path):
    tmp_db.save_repositories([sample_repo])
    _label(tmp_db, sample_repo)
    tmp_db.conn.execute('INSERT INTO evidence_classification_tags VALUES (?,?,?,?,?,?,?,NULL)',
        (sample_repo.html_url, 1, 'anthropic/claude-haiku-5.5', 'notifications',
         sample_repo.description, 'Fixture support rationale', 'description'))
    tmp_db.conn.execute('UPDATE evidence_classifications SET domain=NULL, kind=NULL')
    tmp_db.conn.commit()
    item = {'html_url': sample_repo.html_url, 'taxonomy_version': 1,
            'model': 'anthropic/claude-haiku-5.5', 'tag_id': 'notifications',
            'reason': 'Publication review: support caveat, retained as unknown'}
    directory = tmp_path / 'reviewed'
    manifest = _prepare()(tmp_db.db_path, directory, 'a'*40, allow_partial=True,
                          review_exclusions=[item])
    with sqlite3.connect(directory / 'govtech.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidence_classification_tags').fetchone()[0] == 0
        row = conn.execute('SELECT status, abstentions_json FROM evidence_classifications').fetchone()
        assert row[0] == 'abstained'
        assert json.loads(row[1])['publication_review'][0]['tag_id'] == 'notifications'
    assert tmp_db.conn.execute('SELECT COUNT(*) FROM evidence_classification_tags').fetchone()[0] == 1
    assert manifest['classification']['labelled_repos'] == 0
    assert manifest['classification']['review_exclusions'] == [item]
    publication.validate_bundle(directory)


def test_no_classification_cannot_publish_as_classified(tmp_db,sample_repo,tmp_path):
    prepare=_prepare()
    tmp_db.save_repositories([sample_repo])
    with pytest.raises(ValueError,match='evidence'):
        prepare(tmp_db.db_path,tmp_path/'empty','a'*40,allow_partial=True)
