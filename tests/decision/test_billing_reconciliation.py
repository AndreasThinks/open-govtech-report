import pytest

from govtech_scraper.decision.evidence import DEFAULT_MODEL, OpenRouterBudget
from govtech_scraper.decision.reconcile import (
    GenerationLookupError,
    GenerationReconciler,
    ReconcileResult,
)


class FakeGenerationTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def get_generation(self, generation_id, *, api_key, timeout):
        self.requests.append({"generation_id": generation_id, "api_key": api_key, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def reserve_uncertain(db, *, request_hash="hash", generation_id="gen-abc", reserved=0.005):
    budget = OpenRouterBudget(db, ceiling_usd=1.0, run_id="run")
    claim = budget.reserve(
        model=DEFAULT_MODEL,
        request_hash=request_hash,
        estimated_input_tokens=100,
        max_output_tokens=100,
    )
    db.conn.execute(
        "UPDATE openrouter_budget_ledger SET reserved_usd=?, status='uncertain', error='missing cost' WHERE id=?",
        (reserved, claim.id),
    )
    budget.save_response_receipt(
        claim_id=claim.id,
        request_hash=request_hash,
        model=DEFAULT_MODEL,
        response={"id": generation_id, "usage": {"prompt_tokens": 1}},
        usage={},
        source_url="https://github.com/example/repo",
        input_hash="input-hash",
    )
    db.conn.commit()
    return claim


async def test_generation_lookup_fake_404_then_200_reconciles_atomically(tmp_db):
    claim = reserve_uncertain(tmp_db)
    transport = FakeGenerationTransport(
        [
            {"status": 404, "data": {"error": "not ready"}},
            {
                "status": 200,
                "data": {
                    "id": "gen-abc",
                    "model": "anthropic/claude-haiku-5.5",
                    "data": {"total_cost": 0.00123},
                    "usage": {"cost": 0.00123},
                },
            },
        ]
    )
    result = await GenerationReconciler(tmp_db, api_key="key", transport=transport, sleep=lambda _: None).reconcile(
        request_hash="hash", max_attempts=2
    )
    assert result == ReconcileResult.RECONCILED
    row = tmp_db.conn.execute("SELECT status, actual_usd FROM openrouter_budget_ledger WHERE id=?", (claim.id,)).fetchone()
    assert row["status"] == "completed"
    assert row["actual_usd"] == pytest.approx(0.00123)


async def test_bad_id_mismatch_unavailable_cost_and_cost_breach_are_rejected(tmp_db):
    reserve_uncertain(tmp_db, request_hash="bad-id", generation_id="not a valid id")
    with pytest.raises(GenerationLookupError, match="invalid generation id"):
        await GenerationReconciler(tmp_db, api_key="key", transport=FakeGenerationTransport([])).reconcile(
            request_hash="bad-id"
        )

    cases = {
        "mismatch": {"id": "gen-other", "data": {"total_cost": 0.001}},
        "missing-cost": {"id": "gen-abc", "data": {}},
        "over": {"id": "gen-abc", "data": {"total_cost": 0.01}},
    }
    for name, data in cases.items():
        reserve_uncertain(tmp_db, request_hash=name, generation_id="gen-abc", reserved=0.005)
        result = await GenerationReconciler(
            tmp_db,
            api_key="key",
            transport=FakeGenerationTransport([{"status": 200, "data": data}]),
            sleep=lambda _: None,
        ).reconcile(request_hash=name)
        assert result == ReconcileResult.HELD
        row = tmp_db.conn.execute(
            "SELECT status, actual_usd FROM openrouter_budget_ledger WHERE request_hash=?", (name,)
        ).fetchone()
        assert row["status"] == "uncertain"
        assert row["actual_usd"] is None


async def test_duplicate_reconciler_process_is_idempotent(tmp_db):
    reserve_uncertain(tmp_db, request_hash="same", generation_id="gen-same")
    payload = {"status": 200, "data": {"id": "gen-same", "data": {"total_cost": 0.001}}}
    first = await GenerationReconciler(
        tmp_db, api_key="key", transport=FakeGenerationTransport([payload]), sleep=lambda _: None
    ).reconcile(request_hash="same")
    second = await GenerationReconciler(
        tmp_db, api_key="key", transport=FakeGenerationTransport([]), sleep=lambda _: None
    ).reconcile(request_hash="same")
    assert first == ReconcileResult.RECONCILED
    assert second == ReconcileResult.ALREADY_RECONCILED
