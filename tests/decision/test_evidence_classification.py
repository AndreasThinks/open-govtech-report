import json
import math
import sqlite3

import pytest

from govtech_scraper.decision.evidence import (
    BudgetExhausted,
    DEFAULT_MODEL,
    EvidenceClassifier,
    EvidenceSource,
    EvidenceStore,
    OpenRouterBudget,
    OpenRouterClient,
    ValidationError,
    classify_inputs,
    estimate_prompt_tokens_upper_bound,
    response_schema,
    source_from_repo,
    validate_model_output,
)
from govtech_scraper.decision.taxonomy import load_taxonomy
from govtech_scraper.models import Repository


class FakeOpenRouter:
    def __init__(self, response, usage=None):
        self.response = response
        self.usage = usage or {"prompt_tokens": 100, "completion_tokens": 40, "total_cost": 0.0002}
        self.calls = 0

    async def classify(self, messages, *, response_schema, max_output_tokens):
        self.calls += 1
        return self.response, self.usage


class CrashAfterDelivery:
    def __init__(self):
        self.calls = 0

    async def classify(self, messages, *, response_schema, max_output_tokens):
        self.calls += 1
        return _valid_response(), {"prompt_tokens": 100, "completion_tokens": 40, "total_cost": 0.0002}


def _valid_response():
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


def test_validation_rejects_unknown_ids_and_nonliteral_quotes(sample_repo):
    taxonomy = load_taxonomy()
    source = source_from_repo(
        sample_repo,
        readme="GOV.UK Notify - sending emails and SMS",
        readme_blob_sha="sha1",
    )
    bad = _valid_response()
    bad["tags"][0]["id"] = "normalized-not-real"
    with pytest.raises(ValidationError, match="unknown tag"):
        validate_model_output(bad, source, taxonomy)


def test_schema_enforces_taxonomy_enums_and_five_tag_limit(sample_repo):
    taxonomy = load_taxonomy()
    source = source_from_repo(
        sample_repo,
        readme="GOV.UK Notify - sending emails and SMS",
        readme_blob_sha="sha1",
    )
    schema = response_schema(taxonomy)
    assert schema["properties"]["domain"]["properties"]["id"]["enum"] == [
        d.id for d in taxonomy.domains
    ]
    assert schema["properties"]["kind"]["properties"]["id"]["enum"] == [
        k.id for k in taxonomy.kinds
    ]
    assert schema["properties"]["tags"]["maxItems"] == 5

    bad = _valid_response()
    bad["tags"][0]["quote"] = "not literally in the supplied source"
    with pytest.raises(ValidationError, match="quote"):
        validate_model_output(bad, source, taxonomy)

    bad = _valid_response()
    bad["extra"] = "ignored?"
    with pytest.raises(ValidationError, match="unexpected"):
        validate_model_output(bad, source, taxonomy)

    bad = _valid_response()
    bad["tags"] = bad["tags"] * 6
    with pytest.raises(ValidationError, match="at most 5"):
        validate_model_output(bad, source, taxonomy)


async def test_evidence_run_stores_null_probabilities_and_invalidates_on_input_change(
    tmp_db, sample_repos
):
    tmp_db.save_repositories(sample_repos)
    taxonomy = load_taxonomy()
    store = EvidenceStore(tmp_db)
    budget = OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="test-run")
    classifier = EvidenceClassifier(
        client=FakeOpenRouter(_valid_response()),
        budget=budget,
        model="anthropic/claude-haiku-5.5",
        taxonomy=taxonomy,
        prompt_version="evidence-v1",
    )

    first = await classify_inputs(
        tmp_db,
        classifier,
        [source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS")],
    )
    assert first.scored == 1 and first.failed == 0
    row = store.load_current(sample_repos[0].html_url, taxonomy.version, classifier.model)
    assert row is not None
    assert row["confidence_mode"] == "evidence"
    assert row["domain"] == "00"
    assert row["probability"] is None
    tags = store.load_tags(sample_repos[0].html_url, taxonomy.version, classifier.model)
    assert tags == [
        {
            "id": "notifications",
            "probability": None,
            "quote": "sending emails and SMS",
            "rationale": "The taxonomy definition covers notification delivery.",
            "source_field": "description",
        }
    ]

    unchanged = await classify_inputs(
        tmp_db,
        classifier,
        [source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS")],
    )
    assert unchanged.scored == 0 and unchanged.skipped_unchanged == 1

    changed_source = source_from_repo(
        sample_repos[0],
        readme="GOV.UK Notify - sending emails and SMS. New operational runbook.",
        readme_blob_sha="sha2",
    )
    changed = await classify_inputs(tmp_db, classifier, [changed_source])
    assert changed.scored == 1 and changed.skipped_unchanged == 0


async def test_budget_reservation_survives_uncertain_delivery(tmp_db):
    budget = OpenRouterBudget(tmp_db, ceiling_usd=0.01, run_id="uncertain")
    claim = budget.reserve(
        model="anthropic/claude-haiku-5.5",
        request_hash="abc",
        estimated_input_tokens=1000,
        max_output_tokens=100,
    )
    budget.mark_uncertain(claim.id, "timeout after request was sent")
    with pytest.raises(RuntimeError, match="budget|100k"):
        budget.reserve(
            model="anthropic/claude-haiku-5.5",
            request_hash="def",
            estimated_input_tokens=1_000_000,
            max_output_tokens=100,
        )

    rows = tmp_db.conn.execute(
        "SELECT status, reserved_usd, actual_usd FROM openrouter_budget_ledger ORDER BY id"
    ).fetchall()
    assert rows[0]["status"] == "uncertain"
    assert rows[0]["actual_usd"] is None


def test_budget_persists_global_ceiling_and_blocks_duplicate_active_claims(tmp_path):
    path = tmp_path / "budget.db"
    from govtech_scraper.db import Database

    db_a = Database(str(path))
    db_b = Database(str(path))
    try:
        budget_a = OpenRouterBudget(db_a, ceiling_usd=0.50, run_id="pilot")
        claim = budget_a.reserve(
            model=DEFAULT_MODEL,
            request_hash="same-request",
            estimated_input_tokens=100,
            max_output_tokens=100,
        )
        assert claim.reserved_usd > 0

        with pytest.raises(BudgetExhausted, match="active claim"):
            OpenRouterBudget(db_b, ceiling_usd=0.50, run_id="bulk").reserve(
                model=DEFAULT_MODEL,
                request_hash="same-request",
                estimated_input_tokens=100,
                max_output_tokens=100,
            )

        with pytest.raises(BudgetExhausted, match="increase"):
            OpenRouterBudget(db_b, ceiling_usd=0.51, run_id="bulk")
    finally:
        db_a.close()
        db_b.close()


def test_budget_rejects_unknown_model_nonfinite_costs_and_actual_over_reserve(tmp_db):
    budget = OpenRouterBudget(tmp_db, ceiling_usd=0.01, run_id="costs")
    with pytest.raises(BudgetExhausted, match="No validated"):
        budget.reserve(
            model="anthropic/surprise-model",
            request_hash="unknown",
            estimated_input_tokens=10,
            max_output_tokens=10,
        )

    claim = budget.reserve(
        model=DEFAULT_MODEL,
        request_hash="zero",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    budget.complete(claim.id, {"total_cost": 0})
    row = tmp_db.conn.execute(
        "SELECT status, actual_usd FROM openrouter_budget_ledger WHERE id=?", (claim.id,)
    ).fetchone()
    assert row["status"] == "completed"
    assert row["actual_usd"] == 0

    for bad_cost in (-0.1, math.inf, math.nan, None):
        claim = budget.reserve(
            model=DEFAULT_MODEL,
            request_hash=f"bad-{bad_cost}",
            estimated_input_tokens=10,
            max_output_tokens=10,
        )
        usage = {} if bad_cost is None else {"total_cost": bad_cost}
        with pytest.raises(BudgetExhausted):
            budget.complete(claim.id, usage)

    claim = budget.reserve(
        model=DEFAULT_MODEL,
        request_hash="over-reserved",
        estimated_input_tokens=1,
        max_output_tokens=1,
    )
    with pytest.raises(BudgetExhausted, match="exceeded reserved"):
        budget.complete(claim.id, {"total_cost": claim.reserved_usd + 0.01})


async def test_openrouter_client_uses_json_schema_and_usage_accounting():
    seen = {}

    class Transport:
        async def post_json(self, url, *, headers, payload, timeout):
            seen["url"] = url
            seen["payload"] = payload
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(_valid_response()),
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "reasoning_tokens": 0,
                    "total_cost": 0.00001,
                },
            }

    client = OpenRouterClient(api_key="test-key", transport=Transport())
    body, usage = await client.classify(
        [{"role": "user", "content": "classify"}],
        response_schema={"type": "object"},
        max_output_tokens=400,
    )
    assert body["domain"]["id"] == "00"
    assert usage["total_cost"] == pytest.approx(0.00001)
    assert seen["url"].endswith("/chat/completions")
    assert seen["payload"]["response_format"]["type"] == "json_schema"
    assert seen["payload"]["max_tokens"] == 400
    assert seen["payload"]["reasoning"] == {"enabled": False}
    assert seen["payload"]["provider"]["require_parameters"] is True
    assert seen["payload"]["provider"]["allow_fallbacks"] is False
    assert seen["payload"]["provider"]["max_price"] == {"prompt": 0.1, "completion": 0.5}


async def test_malformed_billed_response_is_completed_not_uncertain(tmp_db, sample_repos):
    class Transport:
        async def post_json(self, url, *, headers, payload, timeout):
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": "{not json"}}],
                "usage": {"total_cost": 0.00001},
            }

    budget = OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="malformed")
    classifier = EvidenceClassifier(
        client=OpenRouterClient(api_key="test-key", transport=Transport()),
        budget=budget,
        model=DEFAULT_MODEL,
        taxonomy=load_taxonomy(),
    )
    summary = await classify_inputs(
        tmp_db,
        classifier,
        [source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS")],
    )
    assert summary.failed == 1
    rows = tmp_db.conn.execute("SELECT status, actual_usd FROM openrouter_budget_ledger").fetchall()
    assert [(r["status"], r["actual_usd"]) for r in rows] == [("completed", 0.00001)]


def test_budget_claims_are_atomic_between_connections(tmp_path):
    path = tmp_path / "budget.db"
    conn_a = sqlite3.connect(path)
    conn_a.row_factory = sqlite3.Row
    conn_b = sqlite3.connect(path)
    conn_b.row_factory = sqlite3.Row
    try:
        from govtech_scraper.db import Database

        db_a = Database(str(path))
        db_b = Database(str(path))
        a = OpenRouterBudget(db_a, ceiling_usd=0.000014, run_id="a")
        b = OpenRouterBudget(db_b, ceiling_usd=0.000014, run_id="b")
        a.reserve(
            model="anthropic/claude-haiku-5.5",
            request_hash="a",
            estimated_input_tokens=10,
            max_output_tokens=10,
        )
        with pytest.raises(RuntimeError, match="budget"):
            b.reserve(
                model="anthropic/claude-haiku-5.5",
                request_hash="b",
                estimated_input_tokens=10,
                max_output_tokens=10,
            )
    finally:
        conn_a.close()
        conn_b.close()


async def test_budget_exhaustion_preserves_existing_valid_labels(tmp_db, sample_repos):
    tmp_db.save_repositories(sample_repos)
    taxonomy = load_taxonomy()
    store = EvidenceStore(tmp_db)
    budget = OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="first")
    classifier = EvidenceClassifier(
        client=FakeOpenRouter(_valid_response()),
        budget=budget,
        model=DEFAULT_MODEL,
        taxonomy=taxonomy,
    )
    source = source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS")
    assert (await classify_inputs(tmp_db, classifier, [source])).scored == 1

    exhausted = EvidenceClassifier(
        client=FakeOpenRouter(_valid_response()),
        budget=OpenRouterBudget(tmp_db, ceiling_usd=0.000001, run_id="second"),
        model=DEFAULT_MODEL,
        taxonomy=taxonomy,
    )
    changed_source = source_from_repo(
        sample_repos[0],
        readme="GOV.UK Notify - sending emails and SMS. New budget retry.",
        readme_blob_sha="retry",
    )
    summary = await classify_inputs(tmp_db, exhausted, [changed_source], rescore=True)
    assert summary.stopped_early
    row = store.load_current(sample_repos[0].html_url, taxonomy.version, DEFAULT_MODEL)
    assert row["status"] == "labelled"
    assert store.load_tags(sample_repos[0].html_url, taxonomy.version, DEFAULT_MODEL)


async def test_completed_receipt_is_reused_after_result_crash(tmp_db, sample_repos, monkeypatch):
    taxonomy = load_taxonomy()
    client = CrashAfterDelivery()
    classifier = EvidenceClassifier(
        client=client,
        budget=OpenRouterBudget(tmp_db, ceiling_usd=1.0, run_id="crash"),
        model=DEFAULT_MODEL,
        taxonomy=taxonomy,
    )
    source = source_from_repo(sample_repos[0], readme="GOV.UK Notify - sending emails and SMS")

    original_save = EvidenceStore.save_success

    def crash_once(self, **kwargs):
        raise RuntimeError("crash after billing")

    monkeypatch.setattr(EvidenceStore, "save_success", crash_once)
    first = await classify_inputs(tmp_db, classifier, [source])
    assert first.failed == 1
    assert client.calls == 1

    monkeypatch.setattr(EvidenceStore, "save_success", original_save)
    second = await classify_inputs(tmp_db, classifier, [source])
    assert second.scored == 1
    assert client.calls == 1


def test_prompt_token_upper_bound_uses_utf8_bytes_and_rejects_large_schema():
    messages = [{"role": "user", "content": "é" * 10}]
    assert estimate_prompt_tokens_upper_bound(messages, {"type": "object"}) > len("é" * 10)
    with pytest.raises(BudgetExhausted, match="100k"):
        estimate_prompt_tokens_upper_bound(messages, {"padding": "x" * 100_000})


async def test_sources_from_db_defaults_nonfork_and_reuses_cached_readme(tmp_db, sample_repo):
    from govtech_scraper.decision.evidence import sources_from_db

    fork = Repository(
        owner="gov-org",
        name="forked",
        html_url="https://github.com/gov-org/forked",
        country="United States",
        description="Forked repository",
        fork=True,
        stars=1000,
        pushed_at="2024-06-01T00:00:00Z",
    )
    sample_repo.pushed_at = "2024-06-01T00:00:00Z"
    tmp_db.save_repositories([sample_repo, fork])
    store = EvidenceStore(tmp_db)
    cached = source_from_repo(
        sample_repo,
        readme="Cached README for Open data portal",
        readme_blob_sha="sha-cache",
        readme_status="fetched",
    )
    store.save_source(cached)
    tmp_db.conn.commit()

    sources = await sources_from_db(tmp_db, fetch_readmes=True)
    assert [s.html_url for s in sources] == [sample_repo.html_url]
    assert sources[0].readme_blob_sha == "sha-cache"
    assert sources[0].fields["readme"] == "Cached README for Open data portal"
