from govtech_scraper.decision import evidence
from govtech_scraper.decision.evidence import source_from_repo
from govtech_scraper.decision.taxonomy import load_taxonomy


def test_invalid_domain_does_not_discard_valid_literal_tag(sample_repo):
    validate=getattr(evidence,'validate_available_output',None)
    assert callable(validate),'Independent field abstention validator missing'
    source=source_from_repo(sample_repo,readme='Reusable tools for sending emails and SMS')
    raw={'domain':{'id':'00','quote':'invented quotation','rationale':'Generic component'},'kind':None,
         'tags':[{'id':'notifications','quote':'sending emails and SMS','rationale':'Notification delivery'}],
         'abstentions':{'domain':None,'kind':None,'tags':[]}}
    result=validate(raw,source,load_taxonomy())
    assert result['domain'] is None and result['kind'] is None
    assert [t['id'] for t in result['tags']]==['notifications']
    assert result['tags'][0]['quote']=='sending emails and SMS'
    assert 'validation' in result['abstentions']['domain'].lower()


def test_invalid_tag_is_rejected_not_normalized(sample_repo):
    validate=getattr(evidence,'validate_available_output',None)
    assert callable(validate),'Independent field abstention validator missing'
    raw={'domain':None,'kind':None,'tags':[{'id':' Notifications','quote':'Data','rationale':'Invalid id'}],
         'abstentions':{'domain':None,'kind':None,'tags':[]}}
    result=validate(raw,source_from_repo(sample_repo),load_taxonomy())
    assert result['tags']==[] and result['domain'] is None
    assert result['abstentions']['tags']


async def test_inaccessible_source_is_never_sent_to_model(tmp_db,sample_repo):
    from govtech_scraper.decision.evidence import EvidenceClassifier,OpenRouterBudget,classify_inputs
    class Client:
        calls=0
        async def classify(self,*args,**kwargs):
            self.calls+=1
            raise AssertionError('Inaccessible source sent to paid model')
    client=Client()
    source=source_from_repo(sample_repo,readme_status='inaccessible')
    classifier=EvidenceClassifier(client=client,budget=OpenRouterBudget(tmp_db,ceiling_usd=50,run_id='offline'),model='openai/gpt-6-luna',taxonomy=load_taxonomy())
    result=await classify_inputs(tmp_db,classifier,[source])
    assert client.calls==0 and result.scored==0
