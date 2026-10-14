from contextlib import contextmanager
from govtech_scraper.api.queries import Catalogue
from govtech_scraper.api.insights import Insights
from govtech_scraper.decision.evidence import EvidenceStore, source_from_repo


def test_related_theme_members_are_materialized_before_join(tmp_db, sample_repo):
    tmp_db.save_repositories([sample_repo])
    source = source_from_repo(sample_repo)
    EvidenceStore(tmp_db).save_success(html_url=sample_repo.html_url, taxonomy_version=1,
        model='fixture-evidence', prompt_version='fixture', input_hash='fixture', source=source,
        domain='00', kind='library', tags=[{'id':'notifications',
        'quote':sample_repo.description, 'rationale':'Fixture rationale', 'source_field':'description'}],
        usage={'cost':0})
    catalogue = Catalogue(tmp_db.db_path)
    connect = catalogue._connect
    queries = []
    @contextmanager
    def traced():
        with connect() as conn:
            conn.set_trace_callback(queries.append)
            yield conn
    catalogue._connect = traced
    result = Insights(catalogue).theme('notifications')
    assert result['repos'] == 1
    sql = next(sql for sql in queries if 'AS shared' in sql)
    with connect() as conn:
        plan = [row[3] for row in conn.execute('EXPLAIN QUERY PLAN '+sql)]
    assert 'MATERIALIZE m' in plan, plan
    assert 'MATERIALIZE a' in plan, plan
