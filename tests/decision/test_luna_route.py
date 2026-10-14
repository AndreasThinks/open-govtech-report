from govtech_scraper.decision.evidence import OpenRouterClient,OpenRouterBudget


def test_verified_same_price_luna_route_is_supported(tmp_db):
    client=OpenRouterClient(api_key='offline-fixture',model='openai/gpt-6-luna')
    assert client.model=='openai/gpt-6-luna'
    budget=OpenRouterBudget(tmp_db,ceiling_usd=50,run_id='same-global-cap')
    claim=budget.reserve(model=client.model,request_hash='offline-luna-price',estimated_input_tokens=10000,max_output_tokens=900)
    assert claim.reserved_usd>=10000*0.0000001+900*0.0000005
