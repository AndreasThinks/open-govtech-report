import json

import pytest

from govtech_scraper.decision.evidence import (
    DEFAULT_MODEL,
    BudgetExhausted,
    EvidenceClassifier,
    EvidenceStore,
    OpenRouterBudget,
    OpenRouterClient,
    ProviderResponseError,
    classify_inputs,
    source_from_repo,
)
from govtech_scraper.decision.taxonomy import load_taxonomy


def valid_body():
    return {
        "domain": {
            "id": "00",
            "quote": "sending emails and SMS",
            "rationale": "The repository is a reusable notification service.",
        },
        "kind": {
            "id": "api-service",
            "quote": "GOV.UK Notify",
            "rationale": "It exposes a service API.",
        },
        "tags": [
            {
                "id": "notifications",
                "quote": "sending emails and SMS",
                "rationale": "The taxonomy definition covers notification delivery.",
            }
        ],
        "abstentions": {"domain": None, "kind": None, "tags": []},
    }


class RawTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def post_json(self, url, *, headers, payload, timeout):
        self.requests.append({"url": url, "headers": headers, "payload": payload})
        return self.responses.pop(0)


async def test_200_error_without_usage_is_distinct_from_success_missing_cost():
    error_client = OpenRouterClient(
        api_key="secret",
        transport=RawTransport([{"id": "gen-abc", "error": {"message": "provider failed"}}]),
    )
    with pytest.raises(ProviderResponseError, match="error response") as error:
        await error_client.classify([], response_schema={"type": "object"}, max_output_tokens=10)
    assert error.value.kind == "provider_error"

    success_client = OpenRouterClient(
        api_key="secret",
        transport=RawTransport(
            [
                {
                    "id": "gen-def",
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid_body())}}],
                    "usage": {"prompt_tokens": 1},
                }
            ]
        ),
    )
    with pytest.raises(ProviderResponseError, match="missing verified usage/cost") as missing:
        await success_client.classify([], response_schema={"type": "object"}, max_output_tokens=10)
    assert missing.value.kind == "missing_usage_cost"


async def test_raw_response_and_correlation_are_committed_before_cost_interpretation(
    tmp_db, sample_repos
):
    source = source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS")
    transport = RawTransport(
        [
            {
                "id": "gen-missing-cost",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid_body())}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
                "headers": {"authorization": "do-not-store"},
            }
        ]
    )
    classifier = EvidenceClassifier(
        client=OpenRouterClient(api_key="secret", transport=transport),
        budget=OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="receipt-first"),
        model=DEFAULT_MODEL,
        taxonomy=load_taxonomy(),
    )

    summary = await classify_inputs(tmp_db, classifier, [source])

    assert summary.stopped_early is None
    assert summary.pending == 1
    assert summary.failed == 0
    receipt = tmp_db.conn.execute("SELECT * FROM openrouter_response_receipts").fetchone()
    assert receipt is not None
    assert receipt["source_url"] == source.html_url
    assert receipt["generation_id"] == "gen-missing-cost"
    assert "authorization" not in receipt["response_json"].lower()
    ledger = tmp_db.conn.execute("SELECT status FROM openrouter_budget_ledger").fetchone()
    assert ledger["status"] == "uncertain"


async def test_crash_after_raw_receipt_replays_without_new_post(tmp_db, sample_repos, monkeypatch):
    source = source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS")
    transport = RawTransport(
        [
            {
                "id": "gen-ok",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid_body())}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_cost": 0.0002},
            }
        ]
    )
    classifier = EvidenceClassifier(
        client=OpenRouterClient(api_key="secret", transport=transport),
        budget=OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="crash-raw"),
        model=DEFAULT_MODEL,
        taxonomy=load_taxonomy(),
    )

    original_complete = OpenRouterBudget.complete

    def crash_after_receipt(self, claim_id, usage):
        raise RuntimeError("crash before settlement")

    monkeypatch.setattr(OpenRouterBudget, "complete", crash_after_receipt)
    first = await classify_inputs(tmp_db, classifier, [source])
    assert first.failed == 1
    assert len(transport.requests) == 1

    monkeypatch.setattr(OpenRouterBudget, "complete", original_complete)
    second = await classify_inputs(tmp_db, classifier, [source])
    assert second.scored == 1
    assert len(transport.requests) == 1


async def test_receipts_sanitize_headers_and_do_not_use_shared_last_response_identity(
    tmp_db, sample_repos
):
    class ConcurrentClient:
        def __init__(self):
            self.calls = 0

        async def classify(self, messages, *, response_schema, max_output_tokens):
            self.calls += 1
            return (
                valid_body(),
                {"prompt_tokens": 100, "completion_tokens": 40, "total_cost": 0.0002},
                {
                    "id": f"gen-{self.calls}",
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(valid_body())}}
                    ],
                    "usage": {"total_cost": 0.0002},
                    "headers": {"Authorization": "secret"},
                },
            )

    sources = [
        source_from_repo(repo, readme="GOV.UK Notify - sending emails and SMS")
        for repo in sample_repos[:2]
    ]
    classifier = EvidenceClassifier(
        client=ConcurrentClient(),
        budget=OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="concurrent"),
        model=DEFAULT_MODEL,
        taxonomy=load_taxonomy(),
    )
    summary = await classify_inputs(tmp_db, classifier, sources, concurrency=2)
    assert summary.scored == 2
    receipts = tmp_db.conn.execute(
        "SELECT generation_id, response_json FROM openrouter_response_receipts ORDER BY generation_id"
    ).fetchall()
    assert [row["generation_id"] for row in receipts] == ["gen-1", "gen-2"]
    assert all("secret" not in row["response_json"] for row in receipts)


def test_zero_cost_valid_but_negative_nan_and_missing_are_not_zero(tmp_db):
    budget = OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="costs")
    for value in [0, 0.0]:
        claim = budget.reserve(model=DEFAULT_MODEL, request_hash=f"zero-{value}", estimated_input_tokens=1, max_output_tokens=1)
        budget.complete(claim.id, {"total_cost": value})

    for name, usage in {
        "missing": {},
        "negative": {"total_cost": -1},
        "nan": {"total_cost": float("nan")},
    }.items():
        claim = budget.reserve(model=DEFAULT_MODEL, request_hash=name, estimated_input_tokens=1, max_output_tokens=1)
        with pytest.raises(BudgetExhausted):
            budget.complete(claim.id, usage)


async def test_replaying_a_settled_receipt_leaves_its_ledger_row_unchanged(tmp_db, sample_repos):
    source = source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS")
    transport = RawTransport(
        [
            {
                "id": "gen-ok",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid_body())}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_cost": 0.0002},
            }
        ]
    )
    classifier = EvidenceClassifier(
        client=OpenRouterClient(api_key="secret", transport=transport),
        budget=OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="settled-replay"),
        model=DEFAULT_MODEL,
        taxonomy=load_taxonomy(),
    )
    assert (await classify_inputs(tmp_db, classifier, [source])).scored == 1
    before = [tuple(r) for r in tmp_db.conn.execute("SELECT * FROM openrouter_budget_ledger")]

    await classifier.classify_source(source)

    assert [tuple(r) for r in tmp_db.conn.execute("SELECT * FROM openrouter_budget_ledger")] == before
    assert len(transport.requests) == 1
