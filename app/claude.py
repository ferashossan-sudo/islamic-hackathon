"""Claude (Anthropic API) calls with a JSON-schema output: the router, the dialogue and the verifier.

Claude Haiku 4.5 takes no effort setting. Claude Sonnet 5.5 and Claude Haiku 5.5 take output_config.effort and think
adaptively (thinking counts toward max_tokens); only Sonnet gets the server-side refusal fallback, since Haiku 5.5 has
none. The SDK (1.x) sends no sampling parameters, which Haiku 5.5 would reject.
A refusal or a cut-off reply raises, and the caller falls back (Gemini, then the approved card or the lexical path).
"""
from functools import lru_cache

import anthropic

# USD per million tokens: input, output, cache read, 5-minute cache write (platform.claude.com pricing, 2026-10-05).
# Claude Haiku 5.5 rates are for prompts up to 100K tokens; ours are a few thousand. A model missing here costs 0 in
# the daily budget (limits.py), so a new model is added here before it goes live.
PRICES = {"claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50), "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
          "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125)}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
NO_EFFORT = {"claude-haiku-4-5"}


@lru_cache(maxsize=4)
def _client(key: str) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=key, max_retries=0)  # retries and fallbacks are ours


def _takes_effort(model: str) -> bool:
    return model not in NO_EFFORT


def _has_fallback(model: str) -> bool:
    return not model.startswith("claude-haiku")


def _usd(model: str, usage) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    total = (usage.input_tokens * price[0] + usage.output_tokens * price[1]
             + cache_read * price[2] + cache_write * price[3])
    return round(total / 1_000_000, 6)


async def generate(key: str, model: str, system, payload: str, schema: dict, timeout: float,
                   max_tokens: int) -> tuple[str, dict]:
    """One call; returns the JSON text and the usage for the log. `system` is a string or a list of text blocks
    (the last one may carry cache_control, so the whole system prompt is cached)."""
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    request = {"model": model, "max_tokens": max_tokens, "system": system,
               "messages": [{"role": "user", "content": payload}], "output_config": output_config}
    client = _client(key).with_options(timeout=timeout)
    if _takes_effort(model):
        output_config["effort"] = "low"
    if _has_fallback(model):
        response = await client.beta.messages.create(**request, betas=[FALLBACK_BETA], fallbacks="default")
    else:
        response = await client.messages.create(**request)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise ValueError(response.stop_reason)
    text = next(b.text for b in response.content if b.type == "text")
    u = response.usage
    return text, {"model": response.model, "in": u.input_tokens, "out": u.output_tokens,
                  "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                  "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0, "usd": _usd(model, u)}
