"""Tests for decision-model tagging. No network: the model is a fake System One client."""

import csv

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from govtech_scraper.decision import (
    BackendExhaustedError,
    DecisionStore,
    HttpSystemOneClient,
    load_taxonomy,
    run_decision_tagging,
    score_repos,
)
from govtech_scraper.decision.evaluate import evaluate, read_gold, write_sample
from govtech_scraper.decision.scorer import (
    build_state,
    candidate_domains,
    stage1_request,
    stage2_request,
)
from govtech_scraper.decision.taxonomy import TaxonomyError, parse_taxonomy


class FakeClient:
    """Answers like a System One endpoint. Repos mentioning 'notify' are notifications tools."""

    model = "fake-kev"

    def __init__(self, fail_urls=(), exhaust=False):
        self.calls = []
        self.fail_urls = set(fail_urls)
        self.exhaust = exhaust

    async def decide_many(self, requests):
        if self.exhaust:
            raise BackendExhaustedError("quota")
        self.calls.append(requests)
        return [self._answer(r) for r in requests]

    def _answer(self, request):
        state = request["state"]
        if state["repository"] in self.fail_urls:
            return {"error": "HTTP 422"}
        text = " ".join(state.values()).lower()
        answers = {}
        for qid, q in request["questions"].items():
            if q["type"] == "choice":
                options = list(q["criteria"])
                pick = options[0]
                if qid == "domain" and "notify" not in text:
                    pick = "Health"
                if qid == "kind":
                    pick = "api-service"
                probs = {o: (0.7 if o == pick else 0.3 / (len(options) - 1)) for o in options}
                answers[qid] = {
                    "type": "choice",
                    "choice": pick,
                    "probabilities": probs,
                    "confidence": 0.6,
                }
            else:
                p = (
                    0.9
                    if (qid == "tag:notifications" and "notify" in text) or qid == "substantive"
                    else 0.1
                )
                answers[qid] = {"type": "noul", "noul": p}
        return {"answers": answers}


@pytest.fixture
def taxonomy():
    return load_taxonomy()


def test_shipped_taxonomy_is_valid(taxonomy):
    assert [d.id for d in taxonomy.domains] == [f"{i:02d}" for i in range(11)]
    assert len({t.id for t in taxonomy.tags}) == len(taxonomy.tags)
    assert taxonomy.tag("notifications").domain_id == "00"


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"version": 1, "domains": [], "kinds": [{"id": "x", "definition": "d"}]}, "no domains"),
        (
            {
                "version": 1,
                "domains": [
                    {
                        "id": "1",
                        "name": "n",
                        "definition": "d",
                        "tags": [{"id": "a", "definition": "d"}],
                    }
                ],
                "kinds": [{"id": "x", "definition": "d"}],
            },
            "two digits",
        ),
        (
            {
                "version": 1,
                "domains": [
                    {
                        "id": "01",
                        "name": "n",
                        "definition": "d",
                        "tags": [{"id": "a", "definition": "d"}, {"id": "a", "definition": "e"}],
                    }
                ],
                "kinds": [{"id": "x", "definition": "d"}],
            },
            "duplicate",
        ),
        (
            {
                "version": 1,
                "domains": [
                    {
                        "id": "01",
                        "name": "n",
                        "definition": "d",
                        "tags": [{"id": "Bad Tag", "definition": "d"}],
                    }
                ],
                "kinds": [{"id": "x", "definition": "d"}],
            },
            "hyphenated",
        ),
    ],
)
def test_bad_taxonomies_are_rejected(raw, message):
    with pytest.raises(TaxonomyError, match=message):
        parse_taxonomy(raw)


def test_state_leaves_out_empty_fields_and_truncates_readme(sample_repo):
    sample_repo.license = None
    state = build_state(sample_repo, readme="x" * 5000)
    assert state["repository"] == "gov-org/data-portal"
    assert state["topics"] == "open-data, government, api"
    assert state["readme"].endswith("[truncated]")
    assert "status" not in state


def test_stage_requests_use_taxonomy_options(sample_repo, taxonomy):
    state = build_state(sample_repo)
    first = stage1_request(state, taxonomy)
    assert set(first["questions"]["domain"]["criteria"]) == {d.name for d in taxonomy.domains}
    second = stage2_request(state, taxonomy, ["07"])
    asked = {q[4:] for q in second["questions"]}
    expected = {t.id for t in taxonomy.domain("07").tags} | {
        t.id for t in taxonomy.domain("00").tags
    }
    assert asked == expected


def test_candidate_domains_keeps_top_and_likely_ones():
    probs = {"07": 0.5, "10": 0.3, "01": 0.1, "04": 0.1}
    assert candidate_domains(probs) == ["07", "10"]
    assert candidate_domains({"07": 0.05, "10": 0.04}) == ["07"]
    assert candidate_domains(probs, max_domains=1) == ["07"]


async def test_score_repos_maps_answers_to_taxonomy_ids(sample_repos, taxonomy):
    results = await score_repos(sample_repos, FakeClient(), taxonomy)
    notify = results[0]
    assert notify.error is None
    assert notify.domain == "00"
    assert notify.kind == "api-service"
    assert notify.tags() == ["notifications"]
    assert results[1].domain == "07"
    assert results[1].unclassified()


async def test_score_repos_isolates_failures(sample_repos, taxonomy):
    client = FakeClient(fail_urls={"us-gsa/sam-api"})
    results = await score_repos(sample_repos, client, taxonomy)
    assert results[1].error.startswith("stage 1")
    assert results[0].error is None
    assert len(client.calls[1]) == 2  # failed repo is not sent to stage 2


async def test_run_stores_results_and_skips_unchanged(tmp_db, sample_repos, taxonomy):
    tmp_db.save_repositories(sample_repos)
    client = FakeClient()
    first = await run_decision_tagging(tmp_db, client, taxonomy)
    assert first.scored == 3

    stored = DecisionStore(tmp_db).load(
        "https://github.com/uk-gov/notify", taxonomy.version, client.model
    )
    assert stored.tags() == ["notifications"]
    assert stored.domain_probabilities["00"] == pytest.approx(0.7)

    second = await run_decision_tagging(tmp_db, client, taxonomy)
    assert second.scored == 0 and second.skipped_unchanged == 3

    sample_repos[0].description = "Changed description for notify"
    tmp_db.save_repositories(sample_repos)
    third = await run_decision_tagging(tmp_db, client, taxonomy)
    assert third.scored == 1


async def test_run_stops_cleanly_when_backend_is_exhausted(tmp_db, sample_repos, taxonomy):
    tmp_db.save_repositories(sample_repos)
    summary = await run_decision_tagging(tmp_db, FakeClient(exhaust=True), taxonomy)
    assert summary.stopped_early and summary.scored == 0


async def test_eval_scores_against_gold(tmp_db, tmp_path, sample_repos, taxonomy):
    tmp_db.save_repositories(sample_repos)
    await run_decision_tagging(tmp_db, FakeClient(), taxonomy)
    gold_path = tmp_path / "gold.csv"
    assert write_sample(tmp_db, gold_path, n=10) == 3

    rows = read_gold(gold_path)
    for row in rows:
        notify = "notify" in row["html_url"]
        row["gold_domain"] = "00" if notify else "07"
        row["gold_kind"] = "api-service"
        row["gold_tags"] = "notifications" if notify else "health-services"
    with open(gold_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    report = evaluate(read_gold(gold_path), DecisionStore(tmp_db), taxonomy.version, "fake-kev")
    assert report.scored_rows == 3
    assert report.domain_accuracy == 1.0
    at_half = next(s for s in report.tag_scores if s.threshold == 0.5)
    assert at_half.precision == 1.0
    assert at_half.recall == pytest.approx(1 / 3)


async def test_http_client_retries_then_succeeds():
    attempts = {"n": 0}

    async def handler(request):
        body = await request.json()
        assert body["model"] == "kev-latest"
        attempts["n"] += 1
        if attempts["n"] == 1:
            return web.Response(status=529)
        return web.json_response({"answers": {"q": {"type": "noul", "noul": 0.8}}})

    app = web.Application()
    app.router.add_post("/v1/systemone", handler)
    async with TestServer(app) as server:
        client = HttpSystemOneClient(
            base_url=str(server.make_url("")), model="kev-latest", max_attempts=3
        )
        [response] = await client.decide_many([{"state": "x", "questions": {}}])
    assert response["answers"]["q"]["noul"] == 0.8
    assert attempts["n"] == 2


async def test_http_client_raises_on_payment_required():
    async def handler(request):
        return web.Response(status=402)

    app = web.Application()
    app.router.add_post("/v1/systemone", handler)
    async with TestServer(app) as server:
        client = HttpSystemOneClient(base_url=str(server.make_url("")), api_key="k")
        with pytest.raises(BackendExhaustedError):
            await client.decide_many([{"state": "x", "questions": {}}])
