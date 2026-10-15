from dataclasses import replace

import pytest

from govtech_scraper.decision.evidence import (DEFAULT_MODEL, DEFAULT_PROMPT_VERSION,
    EvidenceStore, OpenRouterBudget, source_from_repo, taxonomy_digest)
from govtech_scraper.decision.taxonomy import load_taxonomy
from govtech_scraper.decision.incremental import IncrementalPlanner, PlanAction, meaningful_hash, merge_public_metadata
from govtech_scraper.db import Database


def seed(db, repo, status='failed'):
    source = source_from_repo(repo, 'bounded evidence', 'old-sha')
    store = EvidenceStore(db)
    store.save_source(source)
    store.save_failure(html_url=repo.html_url, taxonomy_version=load_taxonomy().version,
        model=DEFAULT_MODEL, prompt_version=DEFAULT_PROMPT_VERSION,
        input_hash=source.input_hash(DEFAULT_MODEL, DEFAULT_PROMPT_VERSION, load_taxonomy()),
        source=source, error='invalid response', taxonomy_sha=taxonomy_digest(load_taxonomy()))
    db.conn.execute('UPDATE evidence_classifications SET status=?', (status,))
    db.conn.commit()
    return source


def planner(db):
    return IncrementalPlanner(db, model=DEFAULT_MODEL, prompt_version=DEFAULT_PROMPT_VERSION,
                              taxonomy=load_taxonomy())


def test_meaningful_identity_ignores_counters_timestamps_blob(sample_repo):
    old = source_from_repo(sample_repo, 'same', 'old')
    new = source_from_repo(replace(sample_repo, stars=999, forks=88, pushed_at='new', updated_at='new'), 'same', 'new')
    assert meaningful_hash(old) == meaningful_hash(new)
    assert meaningful_hash(old) != meaningful_hash(source_from_repo(sample_repo, 'changed'))


def test_legacy_failed_reuse_preserves_inference_and_requires_acquisition(tmp_db, sample_repo):
    old = seed(tmp_db, sample_repo)
    p = planner(tmp_db)
    changed = replace(sample_repo, pushed_at='2026-10-14T00:00:00Z')
    assert p.plan(changed).action == PlanAction.ACQUIRE
    before = tuple(tmp_db.conn.execute('SELECT * FROM evidence_classifications').fetchone())
    new = source_from_repo(changed, 'bounded evidence', 'new-sha')
    assert p.plan(changed, new).action == PlanAction.REUSE
    p.rebind(changed, new)
    assert tuple(tmp_db.conn.execute('SELECT * FROM evidence_classifications').fetchone()) == before
    assert p.plan(changed).action == PlanAction.REUSE
    assert p.plan(changed, source_from_repo(changed, 'different')).action == PlanAction.CLASSIFY


def test_abstention_and_prompt_changes(tmp_db, sample_repo):
    source = seed(tmp_db, sample_repo, 'abstained')
    assert planner(tmp_db).plan(sample_repo, source).action == PlanAction.REUSE
    other = IncrementalPlanner(tmp_db, model=DEFAULT_MODEL, prompt_version='new', taxonomy=load_taxonomy())
    assert other.plan(sample_repo, source).action == PlanAction.CLASSIFY


def test_unbound_unknown_claim_quarantines(tmp_db, sample_repo):
    b = OpenRouterBudget(tmp_db, ceiling_usd=50, run_id='old')
    b.reserve(model=DEFAULT_MODEL, request_hash='unknown', estimated_input_tokens=1, max_output_tokens=1)
    assert planner(tmp_db).plan(sample_repo).action == PlanAction.QUARANTINE


def test_quarantine_receipt_lookup_uses_request_hash_index(tmp_db, sample_repo):
    p = planner(tmp_db)
    # Disable transient indexes so this checks the durable schema's lookup path.
    tmp_db.conn.execute('PRAGMA automatic_index=OFF')
    statements = []
    tmp_db.conn.set_trace_callback(statements.append)
    try:
        p._quarantined(sample_repo.html_url)
    finally:
        tmp_db.conn.set_trace_callback(None)
    query, = statements
    details = [row[3] for row in tmp_db.conn.execute('EXPLAIN QUERY PLAN ' + query)]
    receipt_steps = [detail for detail in details
                     if detail.startswith(('SCAN r ', 'SEARCH r '))]
    assert len(receipt_steps) == 1, details
    assert receipt_steps[0].startswith('SEARCH r USING INDEX '), details
    assert '(request_hash=?)' in receipt_steps[0], details


@pytest.mark.parametrize('status', ['reserved', 'uncertain', 'accounting_breach', 'completed'])
@pytest.mark.parametrize(
    'receipt_identity,receipt_source,binding_source,blocked',
    [
        (None, None, None, {'target', 'other'}),
        (None, None, 'target', {'target'}),
        ('matching', 'target', None, {'target'}),
        ('matching', None, None, {'target', 'other'}),
        ('matching', None, 'target', {'target'}),
        ('matching', 'target', 'other', {'target'}),
        ('matching', 'other', 'target', {'other'}),
        ('wrong_claim', 'other', None, {'target', 'other'}),
        ('wrong_hash', 'other', None, {'target', 'other'}),
        ('wrong_claim', 'other', 'target', {'target'}),
        ('wrong_hash', 'other', 'target', {'target'}),
    ],
)
def test_quarantine_receipt_identity_and_source_semantics(
    tmp_db, sample_repo, status, receipt_identity, receipt_source, binding_source, blocked,
):
    p = planner(tmp_db)
    urls = {'target': sample_repo.html_url, 'other': 'https://github.com/example/other'}
    tmp_db.conn.execute('''INSERT INTO openrouter_budget_ledger
        (id, run_id, model, request_hash, status, reserved_usd,
         estimated_input_tokens, max_output_tokens, reserved_at)
        VALUES (1, 'offline', ?, 'pending', ?, 0.01, 1, 1, '2026-01-01')''',
        (DEFAULT_MODEL, status))
    if receipt_identity:
        tmp_db.conn.execute('''INSERT INTO openrouter_response_receipts
            (request_hash, claim_id, model, source_url, response_json, usage_json, received_at)
            VALUES (?, ?, ?, ?, '{}', '{}', '2026-01-01')''',
            ('different' if receipt_identity == 'wrong_hash' else 'pending',
             2 if receipt_identity == 'wrong_claim' else 1,
             DEFAULT_MODEL, urls.get(receipt_source)))
    if binding_source:
        tmp_db.conn.execute('INSERT INTO openrouter_pending_bindings VALUES (?, ?, ?)',
                            ('pending', urls[binding_source], '2026-01-01'))
    tmp_db.conn.commit()
    for name, url in urls.items():
        action = p.plan(replace(sample_repo, html_url=url)).action
        expected = PlanAction.QUARANTINE if status != 'completed' and name in blocked else PlanAction.ACQUIRE
        assert action == expected


def test_metadata_merge_is_idempotent_and_preserves_private_state(tmp_db, tmp_path, sample_repo):
    tmp_db.save_repositories([sample_repo])
    seed(tmp_db, sample_repo)
    before = tuple(tmp_db.conn.execute('SELECT * FROM evidence_classifications').fetchone())
    public = Database(str(tmp_path / 'public.db'))
    public.save_repositories([replace(sample_repo, stars=123)])
    public.conn.execute("UPDATE repositories SET last_scraped='2099-01-01T00:00:00+00:00'")
    public.conn.commit()
    merge_public_metadata(tmp_db, public.db_path)
    merge_public_metadata(tmp_db, public.db_path)
    assert tmp_db.conn.execute('SELECT stars FROM repositories').fetchone()[0] == 123
    assert tmp_db.conn.execute('SELECT COUNT(*) FROM repository_snapshots').fetchone()[0] == 2
    assert tuple(tmp_db.conn.execute('SELECT * FROM evidence_classifications').fetchone()) == before
    public.conn.execute("UPDATE repositories SET stars=1,last_scraped='2000-01-01T00:00:00+00:00'")
    public.conn.commit()
    merge_public_metadata(tmp_db, public.db_path)
    assert tmp_db.conn.execute('SELECT stars FROM repositories').fetchone()[0] == 123


def test_history_merge_lookup_constrains_url_and_timestamp(tmp_db, tmp_path, sample_repo):
    # Model a scrape-wide timestamp bucket, without production data or timings.
    tmp_db.conn.executemany('''INSERT INTO repository_snapshots
        (html_url, owner, name, country, scrape_timestamp)
        VALUES (?, 'synthetic', 'repo', 'Test', '2026-01-01')''',
        ((f'https://github.com/synthetic/repo-{i}',) for i in range(20000)))
    tmp_db.conn.commit()
    tmp_db.conn.execute('PRAGMA automatic_index=OFF')
    public = Database(str(tmp_path / 'public.db'))
    try:
        public.save_repositories([sample_repo])
        public.conn.execute("UPDATE repository_snapshots SET scrape_timestamp='2026-01-01'")
        public.conn.commit()
        statements = []
        tmp_db.conn.set_trace_callback(statements.append)
        try:
            merge_public_metadata(tmp_db, public.db_path)
        finally:
            tmp_db.conn.set_trace_callback(None)
        query, = [sql for sql in statements
                  if sql.startswith('INSERT OR IGNORE INTO repository_snapshots')]
        details = [row[3] for row in tmp_db.conn.execute('EXPLAIN QUERY PLAN ' + query)]
        lookups = [detail for detail in details if 'repository_snapshots' in detail]
        assert len(lookups) == 1, details
        assert 'SEARCH repository_snapshots USING INDEX ' in lookups[0], details
        assert '(html_url=? AND scrape_timestamp=?)' in lookups[0], details
    finally:
        public.close()


def test_history_merge_exact_value_union_preserves_duplicates(tmp_db, tmp_path, sample_repo):
    from collections import Counter

    tmp_db.save_repositories([sample_repo])
    columns = [row[1] for row in tmp_db.conn.execute('PRAGMA table_info(repository_snapshots)')
               if row[1] != 'id']
    names = ','.join(columns)
    insert = f'INSERT INTO repository_snapshots ({names}) VALUES ({",".join("?" for _ in columns)})'
    base = tuple(tmp_db.conn.execute(f'SELECT {names} FROM repository_snapshots').fetchone())
    tmp_db.conn.execute(insert, base)  # Existing duplicates must survive.
    tmp_db.conn.commit()
    public = Database(str(tmp_path / 'public.db'))
    try:
        variants = []
        for i, column in enumerate(columns):
            variant = list(base)
            variant[i] = base[i] + 1 if isinstance(base[i], int) else str(base[i]) + '-different'
            variants.append(tuple(variant))
            if column in {'stars', 'forks', 'watchers', 'open_issues', 'commit_count'}:
                variant[i] = None
                variants.append(tuple(variant))
        # Public IDs overlap private IDs but represent different values. Repeated
        # public rows and NULL values must deduplicate by every non-ID column.
        public.conn.executemany(insert, variants + [base] + variants)
        public.conn.commit()
        expected = Counter([base, base] + list(set(variants) - {base}))
        for _ in range(2):
            merge_public_metadata(tmp_db, public.db_path)
            actual = Counter(tuple(row) for row in tmp_db.conn.execute(f'SELECT {names} FROM repository_snapshots'))
            assert actual == expected
    finally:
        public.close()


def test_rebound_freshness_api_and_publication(tmp_db, tmp_path, sample_repo):
    from govtech_scraper.api.queries import Catalogue
    from govtech_scraper.api.insights import Insights
    from govtech_scraper.publication import prepare_classification_publication
    tmp_db.save_repositories([sample_repo])
    seed(tmp_db, sample_repo, 'abstained')
    changed = replace(sample_repo, pushed_at='2026-10-14T00:00:00Z', updated_at='2026-10-14T00:00:00Z')
    tmp_db.save_repositories([changed])
    assert Catalogue(tmp_db.db_path).get_repo(changed.owner, changed.name)['classification'] is None
    planner(tmp_db).rebind(changed, source_from_repo(changed, 'bounded evidence', 'new'))
    catalogue = Catalogue(tmp_db.db_path)
    assert catalogue.get_repo(changed.owner, changed.name)['classification'] is not None
    assert Insights(catalogue).overview()['classification']['assessed_repos'] == 1
    prepare_classification_publication(tmp_db.db_path, tmp_path / 'bundle', 'a' * 40)
    assert Catalogue(str(tmp_path / 'bundle/govtech.db')).get_repo(changed.owner, changed.name)['classification'] is not None
    # Proofs cannot bless a different inference or later meaningful metadata.
    tmp_db.conn.execute("UPDATE repositories SET description='different purpose'")
    tmp_db.conn.commit()
    assert Catalogue(tmp_db.db_path).get_repo(changed.owner, changed.name)['classification'] is None


async def test_incremental_executor_never_reposts_recent_failed_unchanged_input(tmp_db, sample_repo):
    from govtech_scraper.decision.incremental import classify_incremental
    from govtech_scraper.decision.evidence import EvidenceClassifier
    seed(tmp_db, sample_repo)
    class NeverClient:
        async def classify(self, *args, **kwargs):
            raise AssertionError('must not POST')
    classifier = EvidenceClassifier(client=NeverClient(),
        budget=OpenRouterBudget(tmp_db, ceiling_usd=50, run_id='test'),
        model=DEFAULT_MODEL, taxonomy=load_taxonomy())
    changed = replace(sample_repo, updated_at='2026-10-14T00:00:00Z')
    result = await classify_incremental(tmp_db, classifier, [changed])
    assert result.skipped_unchanged == 1
    assert tmp_db.conn.execute('SELECT COUNT(*) FROM openrouter_budget_ledger').fetchone()[0] == 0


def test_reviewed_exclusions_survive_metadata_rebase_and_publication(tmp_db, tmp_path, sample_repo):
    import json
    from govtech_scraper.publication import prepare_classification_publication
    from govtech_scraper.api.queries import Catalogue
    tmp_db.save_repositories([sample_repo])
    source = seed(tmp_db, sample_repo, 'labelled')
    tmp_db.conn.execute("INSERT INTO evidence_classification_tags VALUES (?, ?, ?, 'open-data', 'bounded evidence', 'reviewed', 'readme', NULL)",
                        (sample_repo.html_url, load_taxonomy().version, DEFAULT_MODEL))
    tmp_db.conn.commit()
    public = Database(str(tmp_path / 'reviewed.db'))
    public.save_repositories([sample_repo])
    seed(public, sample_repo, 'abstained')
    public.conn.execute('UPDATE evidence_classifications SET abstentions_json=?',
        (json.dumps({'publication_review': [{'tag_id': 'open-data', 'reason': 'not established'}]}),))
    public.conn.commit()
    merge_public_metadata(tmp_db, public.db_path)
    # Publishing the private master must not resurrect the public review exclusion.
    prepare_classification_publication(tmp_db.db_path, tmp_path / 'review-bundle', 'b' * 40)
    detail = Catalogue(str(tmp_path / 'review-bundle/govtech.db')).get_repo(sample_repo.owner, sample_repo.name)
    assert detail['classification']['tags'] == []
    assert detail['classification']['abstentions']['publication_review'][0]['reason'] == 'not established'
    # Private inference evidence stays untouched by review publication.
    assert tmp_db.conn.execute('SELECT COUNT(*) FROM evidence_classification_tags').fetchone()[0] == 1


def test_reuse_rejects_disappearing_literal_quote(tmp_db, sample_repo):
    seed(tmp_db, sample_repo, 'labelled')
    tmp_db.conn.execute("INSERT INTO evidence_classification_tags VALUES (?, ?, ?, 'open-data', ?, 'reviewed', 'updated_at', NULL)",
        (sample_repo.html_url, load_taxonomy().version, DEFAULT_MODEL, sample_repo.updated_at))
    tmp_db.conn.commit()
    changed = replace(sample_repo, updated_at='2026-10-14T00:00:00Z')
    assert planner(tmp_db).plan(changed, source_from_repo(changed, 'bounded evidence')).action == PlanAction.QUARANTINE


def test_legacy_source_archived_before_acquisition_overwrites_cache(tmp_db, sample_repo):
    seed(tmp_db, sample_repo)
    # Simulate a pre-migration ledger lacking source history.
    tmp_db.conn.execute('DELETE FROM evidence_source_history')
    tmp_db.conn.commit()
    changed = replace(sample_repo, pushed_at='2026-10-14T00:00:00Z')
    source = source_from_repo(changed, 'bounded evidence', 'new-sha')
    store = EvidenceStore(tmp_db)
    store.save_source(source)
    tmp_db.conn.commit()
    assert planner(tmp_db).plan(changed, source).action == PlanAction.REUSE


def test_missing_legacy_provenance_does_not_mass_repurchase(tmp_db, sample_repo):
    seed(tmp_db, sample_repo)
    tmp_db.conn.execute('DELETE FROM evidence_source_history')
    tmp_db.conn.execute('DELETE FROM evidence_sources')
    tmp_db.conn.commit()
    changed = replace(sample_repo, updated_at='2026-10-14T00:00:00Z')
    assert planner(tmp_db).plan(changed, source_from_repo(changed, 'bounded evidence')).action == PlanAction.QUARANTINE


async def test_real_classifier_uses_period_budget_and_reuses_on_restart(tmp_db, sample_repos):
    from govtech_scraper.decision.incremental import classify_incremental
    from govtech_scraper.decision.evidence import EvidenceClassifier
    from tests.decision.test_uncertain_request_quarantine import CountingClient
    from tests.decision.test_period_budget import AUTH, budget
    from govtech_scraper.decision.period_budget import approve_authorization
    repo = sample_repos[0]
    source = source_from_repo(repo, 'GOV.UK Notify - sending emails and SMS', 'sha1')
    approve_authorization(tmp_db, AUTH)
    client = CountingClient()
    def classifier():
        return EvidenceClassifier(client=client, budget=budget(tmp_db), model=DEFAULT_MODEL, taxonomy=load_taxonomy())
    first = await classify_incremental(tmp_db, classifier(), [repo], sources=[source])
    assert first.scored == 1
    original = tuple(tmp_db.conn.execute('SELECT * FROM evidence_classifications').fetchone())
    changed = replace(repo, updated_at='2026-10-14T00:00:00Z', pushed_at='2026-10-14T00:00:00Z')
    reused = await classify_incremental(tmp_db, classifier(), [changed],
        sources=[source_from_repo(changed, 'GOV.UK Notify - sending emails and SMS', 'different-sha')])
    assert reused.skipped_unchanged == 1
    assert client.calls == 1
    assert tuple(tmp_db.conn.execute('SELECT * FROM evidence_classifications').fetchone()) == original
    assert tmp_db.conn.execute('SELECT COUNT(*) FROM openrouter_claim_authorizations').fetchone()[0] == 1


def test_changed_meaningful_evidence_with_old_quote_is_eligible(tmp_db, sample_repo):
    seed(tmp_db, sample_repo, 'labelled')
    tmp_db.conn.execute("INSERT INTO evidence_classification_tags VALUES (?, ?, ?, 'open-data', 'bounded evidence', 'reviewed', 'readme', NULL)",
        (sample_repo.html_url, load_taxonomy().version, DEFAULT_MODEL))
    tmp_db.conn.commit()
    assert planner(tmp_db).plan(sample_repo, source_from_repo(sample_repo, 'entirely different purpose')).action == PlanAction.CLASSIFY


def test_merge_advances_completed_scrape_history_without_replacing_newer_records(tmp_db, tmp_path):
    public = Database(str(tmp_path / 'metadata.db'))
    for db in (tmp_db, public):
        db.conn.execute("INSERT INTO scrape_runs VALUES ('run', '2026-10-01', NULL, 1, 0, 0)")
        db.conn.execute("INSERT INTO account_fetches VALUES ('run','gov','failed',0,'transient','2026-10-01')")
        db.conn.commit()
    public.conn.execute("UPDATE scrape_runs SET finished_at='2026-10-02', attempted=1, complete=1")
    public.conn.execute("UPDATE account_fetches SET checked_at='2026-10-02', status='succeeded', repo_count=1, error_type=NULL")
    public.conn.commit()
    merge_public_metadata(tmp_db, public.db_path)
    assert tmp_db.conn.execute('SELECT complete FROM scrape_runs').fetchone()[0] == 1
    assert tmp_db.conn.execute('SELECT status FROM account_fetches').fetchone()[0] == 'succeeded'
    public.conn.execute("UPDATE scrape_runs SET finished_at='2026-10-01', complete=0")
    public.conn.commit()
    merge_public_metadata(tmp_db, public.db_path)
    assert tmp_db.conn.execute('SELECT complete FROM scrape_runs').fetchone()[0] == 1


def test_acquisition_can_replace_corrupt_legacy_cache_without_inventing_provenance(tmp_db, sample_repo):
    seed(tmp_db, sample_repo)
    tmp_db.conn.execute('DELETE FROM evidence_source_history')
    tmp_db.conn.execute("UPDATE evidence_sources SET source_json='not-json'")
    tmp_db.conn.commit()
    changed = replace(sample_repo, updated_at='2026-10-14T00:00:00Z')
    source = source_from_repo(changed, 'bounded evidence')
    EvidenceStore(tmp_db).save_source(source)
    tmp_db.conn.commit()
    assert planner(tmp_db).plan(changed, source).action == PlanAction.QUARANTINE


def test_failed_unchanged_input_is_retried_weekly(tmp_db, sample_repo):
    from datetime import UTC, datetime, timedelta
    source = seed(tmp_db, sample_repo)
    p = planner(tmp_db)
    assert p.plan(sample_repo, source).action == PlanAction.REUSE
    week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    tmp_db.conn.execute('UPDATE evidence_classifications SET classified_at=?', (week_ago,))
    tmp_db.conn.commit()
    plan = p.plan(sample_repo, source)
    assert plan.action == PlanAction.CLASSIFY and 'retry' in plan.reason
    # Only failures are retried; an old abstention of unchanged evidence is kept.
    tmp_db.conn.execute("UPDATE evidence_classifications SET status='abstained'")
    tmp_db.conn.commit()
    assert p.plan(sample_repo, source).action == PlanAction.REUSE
