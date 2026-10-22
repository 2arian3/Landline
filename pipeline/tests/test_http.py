import httpx
import pytest

from pipeline.http import Blocked, Disallowed, PoliteClient


def _client(tmp_path, handler) -> PoliteClient:
    return PoliteClient(contact="team@example.com", min_delay=0, cache_dir=tmp_path,
                        transport=httpx.MockTransport(handler))


def test_identifies_honestly_and_obeys_robots(tmp_path):
    seen_agents = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_agents.append(request.headers["user-agent"])
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="ok")

    client = _client(tmp_path, handler)
    assert client.get("https://example.com/public") == "ok"
    with pytest.raises(Disallowed):
        client.get("https://example.com/private/page")
    assert all("LandlineResearch" in ua and "team@example.com" in ua for ua in seen_agents)
    assert not any("Mozilla" in ua for ua in seen_agents)


def test_stops_when_blocked(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(403, text="no")

    with pytest.raises(Blocked):
        _client(tmp_path, handler).get("https://example.com/listings")


def test_caches_responses(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(200, text="page")

    client = _client(tmp_path, handler)
    client.get("https://example.com/a")
    client.get("https://example.com/a")
    assert calls["n"] == 1
