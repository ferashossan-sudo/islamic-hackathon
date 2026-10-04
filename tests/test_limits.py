"""M14: rate limits after distress, and the daily model-call budget."""
import asyncio

from fastapi.testclient import TestClient

from app import limits, main, pipeline
from app.limits import LIMITER, Limiter
from app.schemas import ChatRequest

client = TestClient(main.app)


def test_session_limit_returns_gentle_429():
    for _ in range(limits.SESSION[0]):
        assert client.post("/api/chat", json={"message": "سؤال", "sid": "s1"}).status_code == 200
    r = client.post("/api/chat", json={"message": "سؤال", "sid": "s1"})
    assert r.status_code == 429
    body = r.json()
    assert body["kind"] == "limit"
    assert body["blocks"][0]["key"] == "rate_limit" and "{المدة}" not in body["blocks"][0]["text"]


def test_distress_is_answered_even_when_limited():
    for _ in range(limits.SESSION[0] + 3):
        client.post("/api/chat", json={"message": "سؤال", "sid": "s2"})
    r = client.post("/api/chat", json={"message": "ما عاد أبي أعيش", "sid": "s2"})
    assert r.status_code == 200 and r.json()["kind"] == "distress"


def test_window_slides():
    lim = Limiter()
    for i in range(limits.SESSION[0]):
        assert lim.check("x", "1.2.3.4", now=1000.0 + i) == 0
    assert lim.check("x", "1.2.3.4", now=1000.0 + 30) > 0
    assert lim.check("x", "1.2.3.4", now=1000.0 + limits.SESSION[1] + 1) == 0


def test_address_is_never_stored_in_clear():
    lim = Limiter()
    lim.check("", "203.0.113.7", now=1.0)
    assert not any("203.0.113.7" in key for key in lim.windows)


def test_daily_model_budget_switches_to_degraded(monkeypatch):
    monkeypatch.setattr(limits, "DAILY_LLM_CALLS", 0)
    assert LIMITER.llm_allowed() is False


def test_selftest_needs_its_token(monkeypatch):
    assert client.get("/api/selftest").status_code == 403
    monkeypatch.setenv("SELFTEST_TOKEN", "t")
    assert client.get("/api/selftest", headers={"x-selftest-token": "wrong"}).status_code == 403
    r = client.get("/api/selftest", headers={"x-selftest-token": "t"})
    assert r.status_code == 200 and r.json()["ok"] is False  # no approved entries in the repo yet
