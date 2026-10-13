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


def test_no_classification_cannot_publish_as_classified(tmp_db,sample_repo,tmp_path):
    prepare=_prepare()
    tmp_db.save_repositories([sample_repo])
    with pytest.raises(ValueError,match='evidence'):
        prepare(tmp_db.db_path,tmp_path/'empty','a'*40,allow_partial=True)
