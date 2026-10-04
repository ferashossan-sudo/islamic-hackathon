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

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import Settings
from app.usage import log_event

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "router_v1.md"
RULES = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_VERSION = "router_v1:" + hashlib.sha256(RULES.encode("utf-8")).hexdigest()[:12]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RECENT_OUTCOMES: deque[bool] = deque(maxlen=20)  # for /health: more than half failing means degraded


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Literal["knowledge", "followup", "distress", "out_of_scope"]
    entry_id: str
    confidence: Literal["high", "medium", "low"]
    oos_reason: Literal["none", "personal_fatwa", "fiqh", "hadith_check", "other_topic", "manipulation"]
    evidence_request: Literal["none", "hadith", "verse"]
    framing: str


def _schema(upper: bool) -> dict:
    """JSON schema for the decision; Gemini takes the OpenAPI subset with upper-case types."""
    t = (lambda name: name.upper()) if upper else (lambda name: name)
    enums = {"route": ["knowledge", "followup", "distress", "out_of_scope"], "confidence": ["high", "medium", "low"],
             "oos_reason": ["none", "personal_fatwa", "fiqh", "hadith_check", "other_topic", "manipulation"],
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


def user_payload(message: str, prev_entry: dict | None) -> str:
    safe = message.replace("<", "‹").replace(">", "›")
    prev = f"{prev_entry['id']} | {prev_entry['question']}" if prev_entry else ""
    return json.dumps({"prev_entry": prev, "message": safe}, ensure_ascii=False)


async def _gemini(s: Settings, system: str, payload: str, timeout: float) -> tuple[str, dict]:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": payload}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 400, "responseMimeType": "application/json",
                             "responseSchema": _schema(upper=True)},
        "safetySettings": [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")],
    }
    if "2.5-flash" in s.router_model:
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(GEMINI_URL.format(model=s.router_model), json=body,
                              headers={"x-goog-api-key": s.gemini_api_key})
    r.raise_for_status()
    data = r.json()
    candidate = (data.get("candidates") or [{}])[0]
    if candidate.get("finishReason") not in (None, "STOP"):
        raise ValueError(f"finish {candidate.get('finishReason')}")
    text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))
    usage = data.get("usageMetadata", {})
    return text, {"in": usage.get("promptTokenCount", 0), "out": usage.get("candidatesTokenCount", 0),
                  "cache_read": usage.get("cachedContentTokenCount", 0), "usd": 0.0}


async def _anthropic(s: Settings, system: str, payload: str, timeout: float) -> tuple[str, dict]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key, timeout=timeout, max_retries=0)
    rules, _, cat = system.partition("\n\n\x1e")
    response = await client.messages.create(
        model=s.router_model, max_tokens=400,
        system=[{"type": "text", "text": rules}, {"type": "text", "text": cat, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": payload}],
        output_config={"format": {"type": "json_schema", "schema": _schema(upper=False)}, "effort": "low"},
    )
    if response.stop_reason == "refusal":
        raise ValueError("refusal")
    text = next(b.text for b in response.content if b.type == "text")
    u = response.usage
    return text, {"in": u.input_tokens, "out": u.output_tokens,
                  "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0, "usd": None}


PROVIDERS = {"gemini": _gemini, "anthropic": _anthropic}


async def decide(message: str, prev_entry: dict | None, entries: list[dict], s: Settings) -> Decision | None:
    """One classification call. None means: use the lexical path."""
    call = PROVIDERS.get(s.router_provider)
    if call is None or not s.router_key or not entries:
        return None
    system = RULES + "\n\n\x1e" + catalog(entries)
    payload = user_payload(message, prev_entry)
    started = perf_counter()
    for attempt, timeout in enumerate((s.router_timeout_s, s.router_retry_timeout_s)):
        try:
            text, usage = await call(s, system, payload, timeout)
            decision = Decision.model_validate_json(text)
            log_event(event="llm", call="router", model=s.router_model, ok=True,
                      ms=round((perf_counter() - started) * 1000), **usage)
            RECENT_OUTCOMES.append(True)
            return decision
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            retryable = isinstance(exc, httpx.TransportError) or exc.response.status_code >= 500
            if attempt == 0 and retryable:
                continue
            break
        except (ValidationError, ValueError, KeyError, StopIteration):
            break
        except Exception:  # noqa: BLE001 - any provider failure falls back to the lexical path
            break
    log_event(event="llm", call="router", model=s.router_model, ok=False, ms=round((perf_counter() - started) * 1000))
    RECENT_OUTCOMES.append(False)
    return None


def health(s: Settings) -> str:
    if not s.llm_enabled or not s.router_key:
        return "off"
    failures = RECENT_OUTCOMES.count(False)
    return "degraded" if failures * 2 > len(RECENT_OUTCOMES) else "up"
