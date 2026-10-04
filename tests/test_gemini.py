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
