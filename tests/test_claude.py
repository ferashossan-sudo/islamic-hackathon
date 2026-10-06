"""Claude calls (app/claude.py): request shape per model, refusals, cost; the Gemini backup; per-role settings."""
import asyncio
import dataclasses
from types import SimpleNamespace

import pytest

from app import claude, config, converse, main, router

SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
          "additionalProperties": False}


class FakeMessages:
    def __init__(self, calls, stop_reason="end_turn"):
        self.calls, self.stop_reason = calls, stop_reason

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=9000,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason=self.stop_reason, model=kwargs["model"], usage=usage,
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text='{"reply": "نعم"}')])


def fake_client(monkeypatch, stop_reason="end_turn"):
    calls = []
    messages = FakeMessages(calls, stop_reason)
    client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))
    client.with_options = lambda **kw: client
    monkeypatch.setattr(claude, "_client", lambda key: client)
    return calls


def test_sonnet_gets_low_effort_the_refusal_fallback_and_no_temperature(monkeypatch):
    calls = fake_client(monkeypatch)
    text, usage = asyncio.run(claude.generate("k", "claude-sonnet-5-5", "rules", "{}", SCHEMA, 10, 4000))
    sent = calls[0]
    assert text == '{"reply": "نعم"}'  # read by block type: the empty thinking block is skipped
    assert sent["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"}
    assert sent["betas"] == [claude.FALLBACK_BETA] and sent["fallbacks"] == "default"
    assert "temperature" not in sent and "thinking" not in sent
    assert usage["usd"] == round((1000 * 2 + 100 * 10 + 9000 * 0.2) / 1_000_000, 6)


def test_haiku_gets_no_effort_and_no_sampling_parameters(monkeypatch):
    calls = fake_client(monkeypatch)
    asyncio.run(claude.generate("k", "claude-haiku-4-5", "rules", "{}", SCHEMA, 10, 600))
    sent = calls[0]
    assert "temperature" not in sent
    assert "effort" not in sent["output_config"] and "betas" not in sent and "fallbacks" not in sent


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_a_refused_or_cut_reply_raises(monkeypatch, stop):
    fake_client(monkeypatch, stop_reason=stop)
    with pytest.raises(ValueError):
        asyncio.run(claude.generate("k", "claude-sonnet-5-5", "rules", "{}", SCHEMA, 10, 4000))


def test_settings_per_role_and_a_gemini_name_left_in_the_dashboard(monkeypatch):
    monkeypatch.setenv("ROUTER_PROVIDER", "anthropic")
    monkeypatch.setenv("ROUTER_MODEL", "gemini-3.5-flash-lite")
    for name in ("CONVERSE_PROVIDER", "VERIFY_PROVIDER", "CONVERSE_MODEL", "VERIFY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    s = config.load_settings()
    assert (s.router_model, s.converse_model, s.verify_model) == ("claude-haiku-4-5", "claude-sonnet-5-5",
                                                                   "claude-haiku-4-5")
    assert (s.converse_provider, s.verify_provider) == ("anthropic", "anthropic")
    assert s.gemini_router_model.startswith("gemini-3.5-flash-lite")  # the backup keeps the dashboard's Gemini name


ROUTER_JSON = ('{"route": "knowledge", "entry_id": "none", "confidence": "low", "oos_reason": "none", '
               '"evidence_request": "none", "framing": ""}')


def test_router_falls_back_to_gemini_when_claude_fails(monkeypatch):
    s = dataclasses.replace(main.settings, router_provider="anthropic", anthropic_api_key="a", gemini_api_key="g",
                            router_model="claude-haiku-4-5", gemini_router_model="gemini-3.5-flash-lite")
    used = []

    async def claude_fails(s, model, system, payload, timeout):
        used.append(model)
        raise RuntimeError("credit exhausted")

    async def gemini_ok(s, model, system, payload, timeout):
        used.append(model)
        return ROUTER_JSON, {"in": 1, "out": 1}

    monkeypatch.setitem(router.PROVIDERS, "anthropic", claude_fails)
    monkeypatch.setitem(router.PROVIDERS, "gemini", gemini_ok)
    entries = [{"id": "kawn-x", "theme": "kawn", "question": "س", "variants": []}]
    decision = asyncio.run(router.decide("سؤال", None, entries, s))
    assert decision is not None and decision.confidence == "low"
    assert used == ["claude-haiku-4-5", "gemini-3.5-flash-lite"]


def test_dialogue_falls_back_to_gemini_and_without_any_key_there_is_no_dialogue(monkeypatch):
    s = dataclasses.replace(main.settings, converse_provider="anthropic", anthropic_api_key="a", gemini_api_key="g",
                            converse_model="claude-sonnet-5-5", gemini_converse_model="gemini-3.1-flash-lite",
                            converse_enabled=True)
    used = []

    async def claude_fails(s, model, payload, timeout):
        used.append(model)
        raise ValueError("refusal")

    async def gemini_ok(s, model, payload, timeout):
        used.append(model)
        return '{"reply": "رد"}', {"in": 1, "out": 1}

    monkeypatch.setitem(converse.PROVIDERS, "anthropic", claude_fails)
    monkeypatch.setitem(converse.PROVIDERS, "gemini", gemini_ok)
    entry = next(iter(main.APPROVED), None) or {"id": "x"}
    monkeypatch.setattr(converse, "payload", lambda message, history, e: "{}")
    assert asyncio.run(converse.compose_reply("سؤال", [], entry, s)) == "رد"
    assert used == ["claude-sonnet-5-5", "gemini-3.1-flash-lite"]
    none = dataclasses.replace(s, anthropic_api_key="", gemini_api_key="")
    assert asyncio.run(converse.compose_reply("سؤال", [], entry, none)) is None
