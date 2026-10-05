"""The free-tier model chain: the next model answers when one is out of quota, busy or closed."""
import asyncio
import json

import httpx
import pytest

from app import gemini

OK = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"a": 1}'}]}}], "usageMetadata": {}}


@pytest.fixture
def server(monkeypatch):
    calls, replies = [], {}
    real = httpx.AsyncClient

    def handler(request):
        model = request.url.path.split("/")[-1].split(":")[0]
        calls.append((model, json.loads(request.content)))
        status, payload = replies.get(model, (200, OK))
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(gemini, "SKIPPED", {})
    return calls, replies


def run(models="m-a,m-b"):
    return asyncio.run(gemini.generate("k", models, "sys", "payload", {"type": "OBJECT"}, 5))


def test_quota_error_falls_through_and_skips_the_model_next_time(server):
    calls, replies = server
    replies["m-a"] = (429, {"error": {"code": 429}})
    text, usage = run()
    assert text == '{"a": 1}' and usage["model"] == "m-b"
    calls.clear()
    run()
    assert [m for m, _ in calls] == ["m-b"]  # m-a is not asked again while skipped


def test_all_models_failing_raises(server):
    _, replies = server
    replies["m-a"] = replies["m-b"] = (503, {"error": {"code": 503}})
    with pytest.raises(httpx.HTTPStatusError):
        run()


def test_thinking_is_turned_off_only_for_full_gemini_models():
    assert "thinkingConfig" in gemini.body("gemini-3.6-flash", "s", "p", {}, 0, 10)["generationConfig"]
    assert "thinkingConfig" not in gemini.body("gemini-3.5-flash-lite", "s", "p", {}, 0, 10)["generationConfig"]
    assert "thinkingConfig" not in gemini.body("gemma-4-26b-a4b-it", "s", "p", {}, 0, 10)["generationConfig"]


def test_a_fenced_json_reply_is_unwrapped(server):
    _, replies = server
    fenced = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '```json\n{"a": 2}\n```'}]}}]}
    replies["m-a"] = (200, fenced)
    assert run()[0] == '{"a": 2}'


def test_a_single_model_from_the_dashboard_still_gets_the_free_tier_fallbacks(monkeypatch):
    from app import config
    monkeypatch.setenv("ROUTER_PROVIDER", "gemini")
    monkeypatch.setenv("ROUTER_MODEL", "gemini-3.5-flash-lite")
    assert config.load_settings().router_model == "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    monkeypatch.setenv("ROUTER_MODEL", "gemini-3.1-flash-lite")
    assert config.load_settings().router_model == "gemini-3.1-flash-lite,gemini-3.5-flash-lite"
    monkeypatch.setenv("ROUTER_PROVIDER", "anthropic")
    monkeypatch.setenv("ROUTER_MODEL", "claude-opus-5-5")
    assert config.load_settings().router_model == "claude-opus-5-5"


def test_a_per_minute_limit_skips_briefly_and_the_daily_quota_for_an_hour():
    minute = httpx.Response(429, json={"error": {"details": [
        {"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"retryDelay": "7s"}]}})
    daily = httpx.Response(429, json={"error": {"details": [
        {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}, {"retryDelay": "30000s"}]}})
    assert gemini.skip_seconds(minute) == 7
    assert gemini.skip_seconds(daily) == gemini.DAILY_QUOTA_SKIP
    assert gemini.skip_seconds(httpx.Response(503)) == 60
