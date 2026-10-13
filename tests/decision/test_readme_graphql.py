from govtech_scraper.decision import evidence


def test_readme_query_batches_explicit_public_repositories(sample_repos):
    build=getattr(evidence,'build_graphql_readme_query',None)
    assert callable(build),'Batched README query builder missing'
    query=build(sample_repos[:2])
    assert query.count('repository(owner:')==2
    assert 'isPrivate' in query and 'HEAD:README.md' in query and 'HEAD:README.rst' in query
    assert 'rateLimit' in query


def test_private_readme_body_is_never_returned(sample_repo):
    decode=getattr(evidence,'decode_graphql_readmes',None)
    assert callable(decode),'Public-only README decoder missing'
    payload={'data':{'r0':{'isPrivate':True,'md':{'text':'private content','oid':'a'*40}}}}
    result=decode(payload,[sample_repo])[0]
    assert result[1] is None and result[2] is None and result[3]=='inaccessible'


def test_graphql_readme_preserves_exact_blob_and_text(sample_repo):
    decode=getattr(evidence,'decode_graphql_readmes',None)
    assert callable(decode),'Public-only README decoder missing'
    payload={'data':{'r0':{'isPrivate':False,'md':{'text':'# Actual README\n','oid':'a'*40,'isTruncated':False}}}}
    repo,text,sha,status=decode(payload,[sample_repo])[0]
    assert repo.html_url==sample_repo.html_url and text=='# Actual README\n' and sha=='a'*40 and status=='fetched'


async def test_rest_readme_checks_repository_visibility_first(sample_repo):
    calls=[]
    class Response:
        status=200
        headers={}
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def json(self): return {'private':True}
    class Session:
        def get(self,url,**kwargs):
            calls.append(url)
            return Response()
    result=await evidence.fetch_readme_source(Session(),sample_repo,token='offline-fixture')
    assert result==(None,None,'inaccessible')
    assert not any(url.endswith('/readme') for url in calls)
