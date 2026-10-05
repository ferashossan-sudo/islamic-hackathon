"""The single model call per message: route, approved entry id or none, confidence, framing.

The model only classifies and selects. Its output is constrained to a JSON schema and validated here;
any error, timeout, refusal or invalid output returns None and the pipeline falls back to the lexical
scorer (degraded mode). The user message travels as a JSON field, never as instructions (G9).
"""
import hashlib
import json
from collections import deque
from pathlib import Path
from time import perf_counter
from typing import Literal

import anthropic
import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app import claude, gemini
from app.config import Settings, first
from app.usage import log_event

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "router_v1.md"
RULES = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_VERSION = "router_v1:" + hashlib.sha256(RULES.encode("utf-8")).hexdigest()[:12]
RECENT_OUTCOMES: deque[bool] = deque(maxlen=20)  # for /health: more than half failing means degraded


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Literal["knowledge", "followup", "distress", "out_of_scope"]
    entry_id: str
    confidence: Literal["high", "medium", "low"]
    oos_reason: Literal["none", "personal_fatwa", "fiqh", "hadith_check", "family_faith", "judging_groups", "other_topic",
                        "manipulation"]
    evidence_request: Literal["none", "hadith", "verse"]
    framing: str


def _schema(upper: bool) -> dict:
    """JSON schema for the decision; Gemini takes the OpenAPI subset with upper-case types."""
    t = (lambda name: name.upper()) if upper else (lambda name: name)
    enums = {"route": ["knowledge", "followup", "distress", "out_of_scope"], "confidence": ["high", "medium", "low"],
             "oos_reason": ["none", "personal_fatwa", "fiqh", "hadith_check", "family_faith", "judging_groups", "other_topic",
                           "manipulation"],
             "evidence_request": ["none", "hadith", "verse"]}
    props = {name: {"type": t("string"), "enum": values} for name, values in enums.items()}
    props["entry_id"] = {"type": t("string")}
    props["framing"] = {"type": t("string")}
    order = ["route", "entry_id", "confidence", "oos_reason", "evidence_request", "framing"]
    schema = {"type": t("object"), "properties": props, "required": order}
    if upper:
        schema["propertyOrdering"] = order
    else:
        schema["additionalProperties"] = False
    return schema


def catalog(entries: list[dict]) -> str:
    """One stable line per approved entry: id | theme | question | variants."""
    lines = [f"{e['id']} | {e['theme']} | {e['question']} | {' ؛ '.join(e.get('variants', []))}"
             for e in sorted(entries, key=lambda e: e["id"])]
    return "Catalog of approved entries (id | theme | question | variants):\n" + "\n".join(lines)


def user_payload(message: str, prev_entry: dict | None, prev_message: str = "") -> str:
    def safe(text: str) -> str:
        return text.replace("<", "‹").replace(">", "›")

    prev = f"{prev_entry['id']} | {prev_entry['question']}" if prev_entry else ""
    return json.dumps({"prev_entry": prev, "prev_message": safe(prev_message[:400]), "message": safe(message)},
                      ensure_ascii=False)


async def _gemini(s: Settings, model: str, system: str, payload: str, timeout: float) -> tuple[str, dict]:
    return await gemini.generate(s.gemini_api_key, model, system, payload, _schema(upper=True), timeout,
                                 max_tokens=400)


async def _anthropic(s: Settings, model: str, system: str, payload: str, timeout: float) -> tuple[str, dict]:
    rules, _, cat = system.partition("\n\n\x1e")
    # The rules and the catalog are the same for every message: cached as one prefix.
    blocks = [{"type": "text", "text": rules}, {"type": "text", "text": cat, "cache_control": {"type": "ephemeral"}}]
    return await claude.generate(s.anthropic_api_key, model, blocks, payload, _schema(upper=False), timeout,
                                 max_tokens=600)


PROVIDERS = {"gemini": _gemini, "anthropic": _anthropic}


async def decide(message: str, prev_entry: dict | None, entries: list[dict], s: Settings,
                 prev_message: str = "") -> Decision | None:
    """One classification call. None means: use the lexical path."""
    if not entries:
        return None
    # The configured provider first; Gemini as the backup when Claude is the provider and its call fails.
    chain = [(s.router_provider, s.router_model)]
    if s.router_provider != "gemini":
        chain.append(("gemini", s.gemini_router_model))
    chain = [(PROVIDERS[p], m) for p, m in chain if p in PROVIDERS and s.key_for(p)]
    if not chain:
        return None
    system = RULES + "\n\n\x1e" + catalog(entries)
    payload = user_payload(message, prev_entry, prev_message)
    started = perf_counter()
    from app.limits import LIMITER

    for call, model in chain:
        for attempt, timeout in enumerate((s.router_timeout_s, s.router_retry_timeout_s)):
            try:
                LIMITER.count_llm_call()
                text, usage = await call(s, model, system, payload, timeout)
                decision = Decision.model_validate_json(text)
                usage.setdefault("model", first(model))
                log_event(event="llm", call="router", ok=True, ms=round((perf_counter() - started) * 1000), **usage)
                RECENT_OUTCOMES.append(True)
                return decision
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = isinstance(exc, httpx.TransportError) or exc.response.status_code >= 500
                if attempt == 0 and retryable:
                    continue
                break
            except (anthropic.APIConnectionError, anthropic.InternalServerError):
                if attempt == 0:
                    continue
                break
            except (ValidationError, ValueError, KeyError, StopIteration):
                break
            except Exception:  # noqa: BLE001 - any provider failure: the next provider, then the lexical path
                break
        log_event(event="llm", call="router", ok=False, model=first(model),
                  ms=round((perf_counter() - started) * 1000))
    RECENT_OUTCOMES.append(False)
    return None


def health(s: Settings) -> str:
    if not s.llm_enabled or not s.router_key:
        return "off"
    failures = RECENT_OUTCOMES.count(False)
    return "degraded" if failures * 2 > len(RECENT_OUTCOMES) else "up"
