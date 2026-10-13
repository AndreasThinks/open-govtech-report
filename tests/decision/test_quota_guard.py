"""ZeroGPU now bills prepaid credits after included quota; refuse overflow by default."""
import io
import json
import math
import urllib.error

import pytest

from govtech_scraper.decision.client import BackendExhaustedError, SpaceClient


class FakeGradio:
    def __init__(self):
        self.calls = 0

    def predict(self, raw, **kwargs):
        self.calls += 1
        return json.dumps([{"answers": {}} for _ in json.loads(raw)])


def client():
    c = SpaceClient("owned/scorer", hf_token="test-not-a-secret", max_attempts=1)
    c._client = FakeGradio()
    return c


def install_quota(monkeypatch, payload):
    def get(request, timeout):
        assert request.full_url == "https://huggingface.co/api/spaces/zero-gpu/quota"
        assert timeout <= 30
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr("urllib.request.urlopen", get)


def test_insufficient_included_quota_never_calls_gpu(monkeypatch):
    c = client()
    install_quota(monkeypatch, {"base": 2400, "current": 10, "overquotaUsed": 0})
    with pytest.raises(BackendExhaustedError, match="quota"):
        c._call([{}])
    assert c._client.calls == 0


@pytest.mark.parametrize("remaining", [None, "2400", float("nan"), float("inf"), -1, True])
def test_malformed_quota_fails_closed(monkeypatch, remaining):
    c = client()
    install_quota(monkeypatch, {"base": 2400, "current": remaining})
    with pytest.raises(BackendExhaustedError):
        c._call([{}])
    assert c._client.calls == 0


def test_quota_network_failure_never_calls_gpu_or_leaks_credentials(monkeypatch):
    c = client()
    def fail(*args, **kwargs):
        raise urllib.error.URLError("secret test-not-a-secret")
    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(BackendExhaustedError) as exc:
        c._call([{}])
    assert "test-not-a-secret" not in str(exc.value)
    assert c._client.calls == 0


def test_sufficient_included_quota_allows_call(monkeypatch):
    c = client()
    install_quota(monkeypatch, {"base": 2400, "current": 1000})
    assert c._call([{}]) == [{"answers": {}}]
    assert c._client.calls == 1


def test_exhausted_run_count_never_calls_gpu(monkeypatch):
    c = client()
    install_quota(monkeypatch, {"base": 2400, "current": 1000, "runs": {"remaining": 0}})
    with pytest.raises(BackendExhaustedError):
        c._call([{}])
    assert c._client.calls == 0


def test_missing_token_never_calls_gpu(monkeypatch):
    c = client()
    c.hf_token = None
    with pytest.raises(BackendExhaustedError):
        c._call([{}])
    assert c._client.calls == 0
