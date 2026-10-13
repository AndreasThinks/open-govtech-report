"""SQL tests are offline and exercise the same cohort builder as every dashboard tab."""
import importlib.util
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]


def helper():
    spec = importlib.util.spec_from_file_location('dashboard_queries', ROOT / 'web' / 'queries.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cohort_db(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    for tag in ('api', 'data'):
        tmp_db.save_repo_tag(sample_repos[0].html_url, tag)
    tmp_db.save_repo_tag(sample_repos[1].html_url, 'api')
    return tmp_db


def test_archived_search_without_other_filters(cohort_db):
    q = helper()
    where, params = q.build_where(q.Filters(include_archived=True, search='Notify'))
    rows = cohort_db.conn.execute('SELECT r.name FROM repositories r ' + where, params).fetchall()
    assert [r[0] for r in rows] == ['notify']


def test_multiple_tags_do_not_duplicate_stars_and_keep_cooccurrences(cohort_db):
    q = helper()
    where, params = q.build_where(q.Filters(tags=('api', 'data')))
    assert cohort_db.conn.execute('SELECT SUM(r.stars) FROM repositories r ' + where, params).fetchone()[0] == 700
    rows = cohort_db.conn.execute('SELECT rt.tag, COUNT(*) FROM repositories r JOIN repository_tags rt USING(html_url) ' + where + ' GROUP BY rt.tag', params).fetchall()
    assert dict(rows) == {'api': 2, 'data': 1}


@pytest.mark.parametrize('filters,expected', [
    ({'countries': ('France',)}, 1), ({'languages': ('Python',)}, 2),
    ({'orgs': ('uk-gov',)}, 1), ({'search': 'nothingmatches'}, 0),
    ({'min_stars': 300}, 1), ({'activity_days': 90}, 0),
    ({'include_forks': False}, 3), ({'tags': ('data',)}, 1),
])
def test_filters(cohort_db, filters, expected):
    q = helper()
    where, params = q.build_where(q.Filters(**filters))
    assert cohort_db.conn.execute('SELECT COUNT(*) FROM repositories r ' + where, params).fetchone()[0] == expected


def test_apptest_smoke_and_interactions(cohort_db, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('GOVTECH_DB_PATH', cohort_db.db_path)
    app = AppTest.from_file(str(ROOT / 'web' / 'app.py'), default_timeout=30).run()
    assert not app.exception
    app.checkbox(key='g_arch').check().run()
    app.text_input(key='exp_search').input('notify').run()
    assert not app.exception
    app.text_input(key='exp_search').input('nothingmatches').run()
    assert not app.exception
    assert app.number_input(key='exp_page').value == 1
    app.text_input(key='exp_search').input('').run()
    app.multiselect(key='g_tag').select('api').run()
    assert not app.exception


def test_apptest_pagination_and_all_filters(cohort_db, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from govtech_scraper.models import Repository
    from datetime import datetime, timezone
    # Clear Streamlit's cross-test function caches before switching databases.
    import streamlit as st
    st.cache_data.clear()
    now = datetime.now(timezone.utc).isoformat()
    cohort_db.save_repositories([Repository('pager', f'data-{i}', f'https://github.com/pager/data-{i}',
        'GB', language='Python', stars=5, fork=(i==0), pushed_at=now, created_at=now)
        for i in range(65)])
    for i in range(2):
        for tag in ('api', 'data'):
            cohort_db.save_repo_tag(f'https://github.com/pager/data-{i}', tag)
    monkeypatch.setenv('GOVTECH_DB_PATH', cohort_db.db_path)
    app = AppTest.from_file(str(ROOT / 'web' / 'app.py'), default_timeout=30).run()
    assert not app.exception
    app.number_input(key='exp_page').set_value(2).run()
    assert not app.exception
    app.multiselect(key='g_tag').set_value(['api', 'data']).run()
    assert not app.exception
    assert app.number_input(key='exp_page').value == 1
    app.multiselect(key='g_country').set_value(['GB']).run()
    app.multiselect(key='g_lang').set_value(['Python']).run()
    app.multiselect(key='g_org').set_value(['pager']).run()
    app.selectbox(key='g_activity').select('Active last 3 months').run()
    app.checkbox(key='g_forks').uncheck().run()
    assert not app.exception
    assert app.metric[0].value == '1'
    app.text_input(key='exp_search').input('nothingmatches').run()
    assert not app.exception
    app.slider(key='g_stars').set_value(500).run()
    assert app.metric[0].value == '0'
    assert not app.exception


def test_apptest_cache_tracks_database_revision(cohort_db, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from govtech_scraper.db import Database
    import streamlit as st
    st.cache_data.clear()
    monkeypatch.setenv('GOVTECH_DB_PATH', cohort_db.db_path)
    app_file = str(ROOT / 'web' / 'app.py')
    first = AppTest.from_file(app_file, default_timeout=30).run()
    assert first.metric[0].value == '3'
    replacement = Database(str(tmp_path / 'new-revision.db'))
    replacement.close()
    monkeypatch.setenv('GOVTECH_DB_PATH', str(tmp_path / 'new-revision.db'))
    second = AppTest.from_file(app_file, default_timeout=30).run()
    assert not second.exception
    assert second.metric[0].value == '0'
