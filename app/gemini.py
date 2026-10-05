"""Gemini REST calls over a chain of free-tier models.

Each free-tier model has its own daily quota (500 requests for gemini-3.5-flash-lite on 4 October), so every
role names a chain: when a model answers 429 (quota), 404 (closed to new users) or 5xx (busy), the next one is
tried, and the failing model is skipped for a while instead of costing every later message a wasted request.
"""
import re
import time

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SAFETY = [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]
# How long a model is skipped after each kind of failure, in seconds. A 429 for the per-minute limit is skipped
# only as long as Google asks (retryDelay); a 429 for the daily quota is skipped for an hour.
SKIP_FOR = {429: 60, 404: 24 * 3600, 500: 60, 502: 60, 503: 60, 504: 60}
DAILY_QUOTA_SKIP = 3600
SKIPPED: dict[str, float] = {}
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def chain(models: str) -> list[str]:
    """'a, b' -> ['a', 'b']."""
    return [m.strip() for m in models.split(",") if m.strip()]


def body(model: str, system: str, payload: str, schema: dict | None, temperature: float, max_tokens: int) -> dict:
    out = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": payload}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        "safetySettings": SAFETY,
    }
    if schema is not None:  # without a schema the reply is plain text (the evaluation baseline)
        out["generationConfig"].update(responseMimeType="application/json", responseSchema=schema)
    # Gemini flash models think by default (slow, and the free quota counts it); lite and Gemma reject the setting.
    if model.startswith("gemini-") and "lite" not in model:
        out["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    return out


def skip_seconds(r: httpx.Response) -> float:
    """How long to leave a failing model alone: the daily quota for an hour, a per-minute limit for its delay."""
    if r.status_code != 429:
        return SKIP_FOR[r.status_code]
    try:
        details = r.json().get("error", {}).get("details", [])
    except ValueError:
        return SKIP_FOR[429]
    quotas = [v.get("quotaId", "") for d in details for v in d.get("violations", [])]
    if any("PerDay" in q for q in quotas):
        return DAILY_QUOTA_SKIP
    delay = next((d.get("retryDelay", "") for d in details if d.get("retryDelay")), "")
    try:
        return min(float(delay.rstrip("s")), SKIP_FOR[429]) if delay else SKIP_FOR[429]
    except ValueError:
        return SKIP_FOR[429]


async def generate(key: str, models: str, system: str, payload: str, schema: dict | None, timeout: float,
                   temperature: float = 0.0, max_tokens: int = 1200) -> tuple[str, dict]:
    """JSON text from the first model in the chain that answers. Raises if none does."""
    last: Exception = RuntimeError("no model available")
    names = chain(models)
    now = time.monotonic()
    # If every model is resting, try the one whose rest ends first (a per-minute limit, not the daily quota).
    ready = [m for m in names if SKIPPED.get(m, 0) <= now] or [min(names, key=lambda m: SKIPPED.get(m, 0))]
    for model in ready:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": key},
                                      json=body(model, system, payload, schema, temperature, max_tokens))
            if r.status_code in SKIP_FOR:
                SKIPPED[model] = time.monotonic() + skip_seconds(r)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            last = exc
            continue
        data = r.json()
        candidate = (data.get("candidates") or [{}])[0]
        if candidate.get("finishReason") not in (None, "STOP"):
            raise ValueError(f"finish {candidate.get('finishReason')}")
        parts = candidate.get("content", {}).get("parts", [])
        text = _FENCE.sub("", "".join(p.get("text", "") for p in parts if not p.get("thought")))
        usage = data.get("usageMetadata", {})
        return text, {"model": model, "in": usage.get("promptTokenCount", 0),
                      "out": usage.get("candidatesTokenCount", 0),
                      "cache_read": usage.get("cachedContentTokenCount", 0), "usd": 0.0}
    raise last
