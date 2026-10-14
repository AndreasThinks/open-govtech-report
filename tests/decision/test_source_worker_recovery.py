import json

import pytest

from govtech_scraper.decision.source_worker import (
    AuthBlocked,
    GraphQLSourceWorker,
    SourceWorkerStatus,
)
from govtech_scraper.decision.evidence import EvidenceStore


class GraphQLResponse:
    def __init__(self, *, status=200, payload=None, text=""):
        self.status = status
        self.payload = payload or {}
        self.text = text
        self.headers = {}


class FakeGraphQLTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def post_graphql(self, query, *, token, timeout):
        self.requests.append({"query": query, "token": token, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def graphql_payload(repos):
    data = {"rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "2099-01-01T00:00:00Z"}}
    for index, repo in enumerate(repos):
        data[f"r{index}"] = {
            "isPrivate": False,
            "md": {"text": f"# README {repo.name}", "oid": f"{index:040d}", "isTruncated": False},
            "rst": None,
        }
    return {"data": data}


async def test_504_then_200_retries_same_query_and_commits_once(tmp_db, sample_repos, tmp_path):
    tmp_db.save_repositories(sample_repos[:2])
    transport = FakeGraphQLTransport(
        [
            GraphQLResponse(status=504, text="gateway timeout"),
            GraphQLResponse(payload=graphql_payload(sample_repos[:2])),
        ]
    )
    status_path = tmp_path / "status.json"
    worker = GraphQLSourceWorker(
        tmp_db,
        token_provider=lambda: "token-value",
        transport=transport,
        status_path=status_path,
        initial_batch_size=40,
        max_attempts_per_batch=2,
        sleep=lambda _: None,
    )

    result = await worker.run(limit=2)

    assert result.phase == "completed"
    assert result.cached == 2
    assert len(transport.requests) == 2
    assert transport.requests[0]["query"] == transport.requests[1]["query"]
    assert {row["html_url"] for row in tmp_db.conn.execute("SELECT html_url FROM evidence_sources")} == {
        repo.html_url for repo in sample_repos[:2]
    }


async def test_repeated_timeouts_split_40_to_20_to_10_and_leave_unresolved_waiting(
    tmp_db, sample_repos, tmp_path
):
    repos = []
    for i in range(40):
        repo = sample_repos[i % len(sample_repos)]
        repos.append(
            type(repo)(
                **{
                    **repo.__dict__,
                    "name": f"repo-{i}",
                    "html_url": f"https://github.com/{repo.owner}/repo-{i}",
                    "pushed_at": f"2024-06-{(i % 28) + 1:02d}T00:00:00Z",
                }
            )
        )
    tmp_db.save_repositories(repos)
    transport = FakeGraphQLTransport([TimeoutError("slow")] * 8)
    worker = GraphQLSourceWorker(
        tmp_db,
        token_provider=lambda: "token",
        transport=transport,
        status_path=tmp_path / "status.json",
        initial_batch_size=40,
        min_batch_size=10,
        max_attempts_per_batch=1,
        max_total_requests=3,
        sleep=lambda _: None,
    )

    result = await worker.run(limit=40)

    assert result.phase == "partial"
    assert result.waiting == 40
    assert [request["query"].count("repository(owner:") for request in transport.requests] == [40, 20, 10]
    assert tmp_db.conn.execute("SELECT COUNT(*) FROM evidence_sources").fetchone()[0] == 0


async def test_private_repositories_are_skipped_and_auth_errors_are_blocked(
    tmp_db, sample_repos, tmp_path
):
    tmp_db.save_repositories(sample_repos[:2])
    private_payload = {
        "data": {
            "r0": {"isPrivate": True, "md": {"text": "secret", "oid": "a" * 40}, "rst": None},
            "r1": {
                "isPrivate": False,
                "md": {"text": "# public", "oid": "b" * 40, "isTruncated": False},
                "rst": None,
            },
            "rateLimit": {"remaining": 100, "cost": 1, "resetAt": "2099-01-01T00:00:00Z"},
        }
    }
    worker = GraphQLSourceWorker(
        tmp_db,
        token_provider=lambda: "token",
        transport=FakeGraphQLTransport([GraphQLResponse(payload=private_payload)]),
        status_path=tmp_path / "private.json",
        sleep=lambda _: None,
    )
    result = await worker.run(limit=2)
    assert result.cached == 1
    saved = EvidenceStore(tmp_db).load_cached_source(sample_repos[0])
    assert saved is None

    blocked = GraphQLSourceWorker(
        tmp_db,
        token_provider=lambda: "token",
        transport=FakeGraphQLTransport([GraphQLResponse(status=401, text="bad token")]),
        status_path=tmp_path / "blocked.json",
        sleep=lambda _: None,
    )
    with pytest.raises(AuthBlocked):
        await blocked.run(limit=1)
    status = json.loads((tmp_path / "blocked.json").read_text())
    assert status["phase"] == "blocked"
    assert status["final_error"]


async def test_crash_after_commit_resumes_from_uncached_inputs(tmp_db, sample_repos, tmp_path):
    tmp_db.save_repositories(sample_repos[:3])
    first = GraphQLSourceWorker(
        tmp_db,
        token_provider=lambda: "token",
        transport=FakeGraphQLTransport([GraphQLResponse(payload=graphql_payload(sample_repos[:1]))]),
        status_path=tmp_path / "first.json",
        initial_batch_size=1,
        sleep=lambda _: None,
    )
    await first.run(limit=1)

    second = GraphQLSourceWorker(
        tmp_db,
        token_provider=lambda: "token",
        transport=FakeGraphQLTransport([GraphQLResponse(payload=graphql_payload(sample_repos[1:3]))]),
        status_path=tmp_path / "second.json",
        initial_batch_size=40,
        sleep=lambda _: None,
    )
    result = await second.run(limit=3)
    assert result.cached == 3
    assert tmp_db.conn.execute("SELECT COUNT(*) FROM evidence_sources").fetchone()[0] == 3


async def test_exception_path_writes_structured_final_status(tmp_db, sample_repo, tmp_path):
    tmp_db.save_repositories([sample_repo])
    status_path = tmp_path / "status.json"
    worker = GraphQLSourceWorker(
        tmp_db,
        token_provider=lambda: "token",
        transport=FakeGraphQLTransport([RuntimeError("transport exploded")]),
        status_path=status_path,
        max_attempts_per_batch=1,
        max_total_requests=1,
        sleep=lambda _: None,
    )
    result = await worker.run(limit=1)
    assert isinstance(result, SourceWorkerStatus)
    status = json.loads(status_path.read_text())
    assert status["phase"] == "partial"
    assert status["final_error"] == "transport exploded"
    assert status["waiting"] == 1
