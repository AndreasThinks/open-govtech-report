import json

import pytest

from govtech_scraper.decision.evidence import (
    DEFAULT_MODEL,
    AccountingBreach,
    EvidenceClassifier,
    OpenRouterBudget,
    PendingRequest,
    classify_inputs,
    source_from_repo,
)
from govtech_scraper.decision.taxonomy import load_taxonomy


def body():
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


class CountingClient:
    def __init__(self):
        self.calls = 0

    async def classify(self, messages, *, response_schema, max_output_tokens):
        self.calls += 1
        return body(), {"total_cost": 0.0002}, {
            "id": f"gen-{self.calls}",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body())}}],
            "usage": {"total_cost": 0.0002},
        }


async def test_duplicate_uncertain_request_is_pending_not_global_halt(tmp_db, sample_repos):
    taxonomy = load_taxonomy()
    budget = OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="seed")
    client = CountingClient()
    classifier = EvidenceClassifier(client=client, budget=budget, model=DEFAULT_MODEL, taxonomy=taxonomy)
    sources = [
        source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS"),
        source_from_repo(sample_repos[1], readme="GOV.UK Notify - sending emails and SMS"),
    ]

    request_hash = classifier.request_hash_for_source(sources[0])
    claim = budget.reserve(
        model=DEFAULT_MODEL,
        request_hash=request_hash,
        estimated_input_tokens=100,
        max_output_tokens=100,
    )
    budget.mark_uncertain(claim.id, "historical missing-cost claim")

    summary = await classify_inputs(tmp_db, classifier, sources, concurrency=1)

    assert summary.pending == 1
    assert summary.scored == 1
    assert summary.stopped_early is None
    assert client.calls == 1


async def test_same_pending_hash_on_restart_does_not_post_again(tmp_db, sample_repos):
    taxonomy = load_taxonomy()
    budget = OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="seed")
    source = source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS")
    classifier = EvidenceClassifier(client=CountingClient(), budget=budget, model=DEFAULT_MODEL, taxonomy=taxonomy)
    request_hash = classifier.request_hash_for_source(source)
    claim = budget.reserve(
        model=DEFAULT_MODEL,
        request_hash=request_hash,
        estimated_input_tokens=100,
        max_output_tokens=100,
    )
    budget.mark_uncertain(claim.id, "claim1276 no receipt")

    restart_client = CountingClient()
    restart = EvidenceClassifier(
        client=restart_client,
        budget=OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="restart"),
        model=DEFAULT_MODEL,
        taxonomy=taxonomy,
    )
    summary = await classify_inputs(tmp_db, restart, [source])
    assert summary.pending == 1
    assert restart_client.calls == 0


def test_two_writers_cannot_raise_global_ceiling(tmp_path):
    from govtech_scraper.db import Database

    db_a = Database(str(tmp_path / "budget.db"))
    db_b = Database(str(tmp_path / "budget.db"))
    try:
        OpenRouterBudget(db_a, ceiling_usd=50.0, run_id="a")
        with pytest.raises(Exception, match="increase"):
            OpenRouterBudget(db_b, ceiling_usd=50.01, run_id="b")
    finally:
        db_a.close()
        db_b.close()


def test_pending_summary_counts_holds_not_assessed(tmp_db):
    budget = OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="summary")
    claim = budget.reserve(model=DEFAULT_MODEL, request_hash="pending", estimated_input_tokens=100, max_output_tokens=100)
    budget.mark_uncertain(claim.id, "missing generation id")
    summary = budget.pending_reconciliation_summary()
    assert summary["pending_count"] == 1
    assert summary["held_usd"] == pytest.approx(claim.reserved_usd)


def test_accounting_breach_remains_global_fatal(tmp_db):
    budget = OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="breach")
    claim = budget.reserve(model=DEFAULT_MODEL, request_hash="breach", estimated_input_tokens=1, max_output_tokens=1)
    with pytest.raises(AccountingBreach):
        budget.complete(claim.id, {"total_cost": 1.5})
    with pytest.raises(AccountingBreach):
        budget.reserve(model=DEFAULT_MODEL, request_hash="after", estimated_input_tokens=1, max_output_tokens=1)


def test_existing_reserved_same_hash_returns_pending_record(tmp_db):
    budget = OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="pending")
    claim = budget.reserve(model=DEFAULT_MODEL, request_hash="same", estimated_input_tokens=1, max_output_tokens=1)
    pending = budget.reserve_or_pending(
        model=DEFAULT_MODEL,
        request_hash="same",
        estimated_input_tokens=1,
        max_output_tokens=1,
    )
    assert isinstance(pending, PendingRequest)
    assert pending.claim_id == claim.id
