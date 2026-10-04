"""The conversational reply: the model talks with the person, persuasively, using ONLY one approved entry.

Verses and hadiths appear only as placeholders that the server fills from the mushaf and the entry.
Every reply passes G13 (guards.check_reply) or is dropped, and the approved card is shown alone.
"""
import hashlib
import json
from pathlib import Path
from time import perf_counter

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app import quran
from app.config import Settings
from app.router import GEMINI_URL
from app.usage import log_event

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "converse_v1.md"
RULES = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_VERSION = "converse_v1:" + hashlib.sha256(RULES.encode("utf-8")).hexdigest()[:12]
MAX_HISTORY = 4
MAX_HISTORY_CHARS = 600


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str


def material(entry: dict) -> dict:
    """What the model may use: the approved entry only, with placeholders for every verse and hadith."""
    labels = {"fact": "حقيقة ثابتة", "leading_theory": "نظرية راجحة", "hypothesis": "فرضية"}
    return {
        "level": entry["level"],
        "question": entry["question"],
        "summary": entry["summary"],
        "explain_simple": entry.get("explain_simple") or "",
        "full_text": entry["body"],
        "science": [{"statement": s["claim"], "degree": labels[s["degree"]]} for s in entry.get("science", [])],
        "tafsir": [{"by": t["mufassir"], "summary": t["summary"]} for t in entry.get("tafsir", [])],
        "verses": [{"placeholder": "{{q:" + ref + "}}", "reference": quran.label(ref),
                    "text": " ".join(v.text for v in quran.lookup(ref))} for ref in entry.get("verses", [])],
        "hadiths": [{"placeholder": "{{h:" + str(i) + "}}", "text": h["text"]}
                    for i, h in enumerate(entry.get("hadiths", []), 1)],
    }


def payload(message: str, history: list[dict], entry: dict) -> str:
    def clean(text: str) -> str:
        return text.replace("<", "‹").replace(">", "›")[:MAX_HISTORY_CHARS]
    turns = [{"role": h["role"], "text": clean(h["text"])} for h in history[-MAX_HISTORY:]]
    return json.dumps({"history": turns, "message": clean(message), "material": material(entry)}, ensure_ascii=False)


SCHEMA_GEMINI = {"type": "OBJECT", "properties": {"reply": {"type": "STRING"}}, "required": ["reply"]}
SCHEMA_ANTHROPIC = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
                    "additionalProperties": False}


VERIFY_RULES = (Path(__file__).resolve().parent / "prompts" / "verify_v1.md").read_text(encoding="utf-8")
VERIFY_SCHEMA_GEMINI = {"type": "OBJECT", "properties": {"unsupported": {"type": "ARRAY", "items": {"type": "STRING"}}},
                        "required": ["unsupported"]}


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unsupported: list[str]


async def _gemini_json(s: Settings, model: str, system: str, payload_text: str, schema: dict, timeout: float,
                       temperature: float) -> tuple[str, dict]:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": payload_text}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 1200, "responseMimeType": "application/json",
                             "responseSchema": schema},
        "safetySettings": [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")],
    }
    if "lite" not in model:  # flash models think by default; lite models reject the setting
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(GEMINI_URL.format(model=model), json=body, headers={"x-goog-api-key": s.gemini_api_key})
    r.raise_for_status()
    data = r.json()
    candidate = (data.get("candidates") or [{}])[0]
    if candidate.get("finishReason") not in (None, "STOP"):
        raise ValueError(f"finish {candidate.get('finishReason')}")
    text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))
    usage = data.get("usageMetadata", {})
    return text, {"in": usage.get("promptTokenCount", 0), "out": usage.get("candidatesTokenCount", 0), "usd": 0.0}


async def _gemini(s: Settings, payload_text: str, timeout: float) -> tuple[str, dict]:
    return await _gemini_json(s, s.converse_model, RULES, payload_text, SCHEMA_GEMINI, timeout, 0.0)


async def _gemini_verify(s: Settings, payload_text: str, timeout: float) -> tuple[str, dict]:
    return await _gemini_json(s, s.verify_model, VERIFY_RULES, payload_text, VERIFY_SCHEMA_GEMINI, timeout, 0.0)


async def _anthropic(s: Settings, payload_text: str, timeout: float) -> tuple[str, dict]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key, timeout=timeout, max_retries=0)
    response = await client.messages.create(
        model=s.converse_model, max_tokens=1200, system=RULES,
        messages=[{"role": "user", "content": payload_text}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_ANTHROPIC}, "effort": "low"},
    )
    if response.stop_reason == "refusal":
        raise ValueError("refusal")
    text = next(b.text for b in response.content if b.type == "text")
    return text, {"in": response.usage.input_tokens, "out": response.usage.output_tokens, "usd": None}


PROVIDERS = {"gemini": _gemini, "anthropic": _anthropic}
VERIFIERS = {"gemini": _gemini_verify}


async def _call(kind: str, fn, s: Settings, payload_text: str, model_cls):
    started = perf_counter()
    try:
        text, usage = await fn(s, payload_text, s.converse_timeout_s)
        value = model_cls.model_validate_json(text)
        log_event(event="llm", call=kind, ok=True, ms=round((perf_counter() - started) * 1000), **usage)
        return value
    except Exception:  # noqa: BLE001 - any provider failure: the approved card is shown alone
        log_event(event="llm", call=kind, ok=False, ms=round((perf_counter() - started) * 1000))
        return None


async def compose_reply(message: str, history: list[dict], entry: dict, s: Settings,
                        feedback: list[str] | None = None) -> str | None:
    """One conversational call. None on any failure; the caller then shows the approved card alone."""
    call = PROVIDERS.get(s.router_provider)
    if call is None or not s.router_key or not s.converse_enabled:
        return None
    text = payload(message, history, entry)
    if feedback:
        data = json.loads(text)
        data["previous_reply_problems"] = ("Your previous reply contained statements that are not in the material. "
                                           "Rewrite it without them: " + " | ".join(feedback))
        text = json.dumps(data, ensure_ascii=False)
    result = await _call("converse", call, s, text, Reply)
    return result.reply.strip() if result else None


async def unsupported_claims(reply: str, entry: dict, s: Settings) -> list[str] | None:
    """G14: a second, strict call lists statements not supported by the approved entry. None if it failed."""
    call = VERIFIERS.get(s.router_provider)
    if call is None:
        return []  # no verifier for this provider: rely on G13 alone
    text = json.dumps({"material": material(entry), "reply": reply}, ensure_ascii=False)
    result = await _call("verify", call, s, text, Verdict)
    return result.unsupported if result else None
