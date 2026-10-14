import pytest
from govtech_scraper.api.queries import Catalogue
from govtech_scraper.api.insights import Insights
from govtech_scraper.decision.evidence import OpenRouterClient,EvidenceStore,source_from_repo,taxonomy_digest
from govtech_scraper.decision.taxonomy import load_taxonomy


async def test_price_caps_are_in_provider_routing_object():
    seen={}
    class Transport:
        async def post_json(self,url,*,headers,payload,timeout):
            seen.update(payload)
            return {'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],'usage':{'cost':0}}
    await OpenRouterClient(api_key='offline-fixture',transport=Transport()).classify(
        [{'role':'user','content':'fixture'}],response_schema={'type':'object'},max_output_tokens=100)
    assert seen['provider'].get('max_price')=={'prompt':0.1,'completion':0.5}
    assert 'temperature' not in seen  # Haiku 5.5 does not advertise this parameter.
    assert 'max_price' not in seen


def test_metadata_hash_cache_is_safe_for_changed_inputs(sample_repo):
    from govtech_scraper.decision.evidence import source_from_repo
    function = Catalogue._evidence_metadata_hash
    assert hasattr(function, 'cache_info'), 'Repeated full-catalogue fingerprints need bounded memoization'
    function.cache_clear()
    names = ('html_url', 'owner', 'name', 'country', 'description', 'language', 'topics',
             'license', 'default_branch', 'fork', 'archived', 'created_at', 'updated_at',
             'pushed_at', 'fork_source', 'stars', 'forks', 'watchers', 'open_issues', 'size_kb')
    import json
    args = [getattr(sample_repo, name) for name in names]
    args[6] = json.dumps(args[6])
    expected = source_from_repo(sample_repo).metadata_hash
    assert function(*args) == expected
    assert function(*args) == expected
    assert function.cache_info().hits == 1
    args[4] = 'A new repository purpose'
    assert function(*args) != expected


def test_abstention_is_unclassified_not_pending(tmp_db,sample_repos):
    tmp_db.save_repositories(sample_repos[:2]);taxonomy=load_taxonomy();store=EvidenceStore(tmp_db)
    for index,repo in enumerate(sample_repos[:2]):
        source=source_from_repo(repo)
        store.save_success(html_url=repo.html_url,taxonomy_version=taxonomy.version,
            model='anthropic/claude-haiku-5.5',prompt_version='evidence-v1',
            input_hash=source.input_hash('anthropic/claude-haiku-5.5','evidence-v1',taxonomy),
            source=source,domain='00' if index==0 else None,kind='library' if index==0 else None,
            tags=[],usage={'cost':0},abstentions={},taxonomy_sha=taxonomy_digest(taxonomy))
    coverage=Insights(Catalogue(tmp_db.db_path)).overview()['classification']
    assert coverage['assessed_repos']==2
    assert coverage['labelled_repos']==1
    assert coverage['unclassified_repos']==1
    assert coverage['abstained_repos']==1
    assert coverage['pending_repos']==0
