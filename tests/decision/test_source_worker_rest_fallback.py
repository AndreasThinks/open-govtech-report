from govtech_scraper.decision.source_worker import GraphQLSourceWorker,GraphQLResponse

async def test_unrecognised_readme_filename_uses_rest_not_false_missing(tmp_db,sample_repo):
    tmp_db.save_repositories([sample_repo])
    class Transport:
        async def post_graphql(self,*args,**kwargs):
            return GraphQLResponse(status=200,payload={'data':{'r0':{'isPrivate':False,'md':None,'rst':None}}})
    worker=GraphQLSourceWorker(tmp_db,token_provider=lambda:'offline',transport=Transport())
    async def rest(repo,token):return '# README.MD exists','a'*40,'fetched'
    worker._fetch_rest_readme=rest
    result=await worker.run(limit=1)
    row=tmp_db.conn.execute('SELECT readme_status,source_json FROM evidence_sources').fetchone()
    assert result.phase=='completed' and row['readme_status']=='fetched'
    assert 'README.MD exists' in row['source_json']
