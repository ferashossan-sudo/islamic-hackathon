"""The conversational reply: the model talks with the person, persuasively, using ONLY one approved entry.

Verses and hadiths appear only as placeholders that the server fills from the mushaf and the entry.
Every reply passes G13 (guards.check_reply) or is dropped, and the approved card is shown alone.
"""
import hashlib
import json
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, ConfigDict, ValidationError

from app import claude, gemini, quran
from app.config import Settings, first
from app.usage import log_event

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "converse_v3.md"
RULES = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_VERSION = "converse_v3:" + hashlib.sha256(RULES.encode("utf-8")).hexdigest()[:12]
MAX_HISTORY = 4
MAX_HISTORY_CHARS = 1200  # a whole previous reply, so the model sees what it already said


# General rules for weighing an apparent conflict between revelation, reason and science, restated from the
# reviewed «بالعقل والعلم» layers of the approved answers (mostly from «بينات»، مركز أصول). The model applies them in
# its own words to any form of a question; they carry no fact of their own.
PRINCIPLES = (
    "القطعي من الوحي والقطعي من العلم لا يتعارضان، لأن الوحي من الله الذي خلق الكون وهو العليم بسننه.",
    "في كلٍّ من الوحي والعلم قطعي وظني: القطعي من أحدهما مقدَّم على الظني من الآخر، والظنيان إذا تعارضا طُلب لهما مرجِّح.",
    "القطع في العلم التجريبي إنما يصح فيما قام على معطى حسي قاطع، ككروية الأرض؛ أما النماذج التي تفسّر الظواهر فدونه رتبة، والعلم يصححها باستمرار.",
    "القرآن ثابت كله قطعاً، لكن دلالة آياته تتفاوت: منها ما لا يُتنازع في فهمه، ومنها ما يحتمل أكثر من معنى؛ فقطعية ثبوت النص لا تجعل كل معنى يُحمل عليه قطعياً. والسنة منها القطعي الثبوت وما دونه.",
    "ما يوهم التعارض بين النص والعلم يرجع إلى خلل في فهم النص أو في تصور العلم، أو إلى جعل ظنٍّ علمي في منزلة الحقيقة؛ فقبل الحكم بالتعارض يُتثبت من ثلاثة: صحة النص، وصحة فهمه، وثبوت الأمر العلمي نفسه.",
    "عدم العلم ليس علماً بالعدم، وسكوت النص عن شيء ليس نفياً له.",
    "ما حدث بعد أن لم يكن لا بد له من محدِث.",
    "هناك فرق بين المستحيل عقلاً، كاجتماع النقيضين، وما لم تجرِ به العادة؛ والمعجزات تخرق العادة ولا تخرق ضرورات العقل.",
    "ليس عيباً في الدين أن يخطئ بعض أتباعه، بل يُرجع عند الحكم عليه إلى أحكامه نفسها.",
    "يُنظر إلى الصورة كاملة لا إلى جزئية مقتطعة، وتُقرأ الآية كاملة في سياقها.",
    "طريقة وصول الاعتقاد إلى الإنسان، كتقليد الآباء أو كثرة القائلين به، لا تحسم صحته؛ وإنما يُوزن كل معتقد بدليله.",
    "الشيء قد يكون فيه نفع ومفسدة أعظم منه فيُنهى عنه، وقد يكون فيه ضرر ونفعه أكبر فيُؤذن فيه.",
)


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str


def reasoning_material(entry: dict, labels: dict[str, str]) -> dict:
    """The «بالعقل والعلم» layer in order: steps (an "ilm" step carries the degree of its science item) and the
    objections with their reviewed answers. Empty lists when the entry has none."""
    reasoning = entry.get("reasoning") or {}
    degrees = {}
    for s in entry.get("science", []):
        degrees.setdefault(s["url"], labels[s["degree"]])
    steps = []
    for s in reasoning.get("steps", []):
        step = {"text": s["text"], "basis": s["basis"]}
        if s["basis"] == "ilm" and s["url"] in degrees:
            step["degree"] = degrees[s["url"]]
        steps.append(step)
    return {"steps": steps,
            "objections": [{"objection": o["objection"], "response": o["response"]}
                           for o in reasoning.get("objections", [])]}


def material(entry: dict) -> dict:
    """What the model may use: the approved entry only, with placeholders for every verse and hadith.

    The same material goes to the G14 verifier, so a reply built from the reasoning layer is checked against it."""
    labels = {"fact": "حقيقة ثابتة", "leading_theory": "نظرية راجحة", "hypothesis": "فرضية"}
    # Reason and science first, the way the reply should go; the sharia texts after them.
    return {
        "level": entry["level"],
        "question": entry["question"],
        "reasoning": reasoning_material(entry, labels),
        "science": [{"statement": s["claim"], "degree": labels[s["degree"]]} for s in entry.get("science", [])],
        "explain_simple": entry.get("explain_simple") or "",
        "summary": entry["summary"],
        "full_text": entry["body"],
        "tafsir": [{"by": t["mufassir"], "summary": t["summary"]} for t in entry.get("tafsir", [])],
        "verses": [{"placeholder": "{{q:" + ref + "}}", "reference": quran.label(ref),
                    "text": " ".join(v.text for v in quran.lookup(ref))} for ref in entry.get("verses", [])],
        "hadiths": [{"placeholder": "{{h:" + str(i) + "}}", "text": h["text"]}
                    for i, h in enumerate(entry.get("hadiths", []), 1)],
        "principles": list(PRINCIPLES),
    }


def payload(message: str, history: list[dict], entry: dict) -> str:
    def clean(text: str) -> str:
        return text.replace("<", "‹").replace(">", "›")[:MAX_HISTORY_CHARS]
    turns = [{"role": h["role"], "text": clean(h["text"])} for h in history[-MAX_HISTORY:]]
    return json.dumps({"history": turns, "message": clean(message), "material": material(entry)}, ensure_ascii=False)


SCHEMA_GEMINI = {"type": "OBJECT", "properties": {"reply": {"type": "STRING"}}, "required": ["reply"]}
SCHEMA_ANTHROPIC = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
                    "additionalProperties": False}


VERIFY_RULES = (Path(__file__).resolve().parent / "prompts" / "verify_v2.md").read_text(encoding="utf-8")
VERIFY_SCHEMA_GEMINI = {"type": "OBJECT", "properties": {"unsupported": {"type": "ARRAY", "items": {"type": "STRING"}}},
                        "required": ["unsupported"]}


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unsupported: list[str]


VERIFY_SCHEMA_ANTHROPIC = {"type": "object", "properties": {"unsupported": {"type": "array", "items": {"type": "string"}}},
                           "required": ["unsupported"], "additionalProperties": False}


async def _gemini(s: Settings, model: str, payload_text: str, timeout: float) -> tuple[str, dict]:
    return await gemini.generate(s.gemini_api_key, model, RULES, payload_text, SCHEMA_GEMINI, timeout)


async def _gemini_verify(s: Settings, model: str, payload_text: str, timeout: float) -> tuple[str, dict]:
    return await gemini.generate(s.gemini_api_key, model, VERIFY_RULES, payload_text, VERIFY_SCHEMA_GEMINI, timeout)


async def _anthropic(s: Settings, model: str, payload_text: str, timeout: float) -> tuple[str, dict]:
    system = [{"type": "text", "text": RULES, "cache_control": {"type": "ephemeral"}}]  # same rules every call
    return await claude.generate(s.anthropic_api_key, model, system, payload_text, SCHEMA_ANTHROPIC, timeout,
                                 max_tokens=4000)  # adaptive thinking counts toward max_tokens


async def _anthropic_verify(s: Settings, model: str, payload_text: str, timeout: float) -> tuple[str, dict]:
    return await claude.generate(s.anthropic_api_key, model, VERIFY_RULES, payload_text, VERIFY_SCHEMA_ANTHROPIC,
                                 timeout, max_tokens=1000)


PROVIDERS = {"gemini": _gemini, "anthropic": _anthropic}
VERIFIERS = {"gemini": _gemini_verify, "anthropic": _anthropic_verify}


def _chain(s: Settings, verify: bool) -> list[tuple]:
    """The configured provider for this role first, then Gemini as the backup when the provider is Claude."""
    from app.limits import LIMITER

    table = VERIFIERS if verify else PROVIDERS
    provider = s.verify_provider if verify else s.converse_provider
    chain = [(provider, s.verify_model if verify else s.converse_model)]
    if provider != "gemini":
        chain.append(("gemini", s.gemini_verify_model if verify else s.gemini_converse_model))
        if not LIMITER.claude_allowed(s.daily_cost_cap_usd):
            chain = chain[1:]  # today's Claude budget is spent: the free backup answers
    return [(table[p], m) for p, m in chain if p in table and s.key_for(p)]


async def _call(kind: str, chain: list[tuple], s: Settings, payload_text: str, model_cls):
    from app.limits import LIMITER

    for fn, model in chain:
        started = perf_counter()
        try:
            LIMITER.count_llm_call()
            text, usage = await fn(s, model, payload_text, s.converse_timeout_s)
            LIMITER.add_cost(usage.get("usd"))
            value = model_cls.model_validate_json(text)
            usage.setdefault("model", first(model))
            log_event(event="llm", call=kind, ok=True, ms=round((perf_counter() - started) * 1000), **usage)
            return value
        except Exception:  # noqa: BLE001 - any provider failure: the next provider, then the approved card alone
            log_event(event="llm", call=kind, ok=False, model=first(model), ms=round((perf_counter() - started) * 1000))
    return None


async def compose_reply(message: str, history: list[dict], entry: dict, s: Settings,
                        feedback: list[str] | None = None) -> str | None:
    """One conversational call. None on any failure; the caller then shows the approved card alone."""
    chain = _chain(s, verify=False)
    if not chain or not s.converse_enabled:
        return None
    text = payload(message, history, entry)
    if feedback:
        data = json.loads(text)
        data["previous_reply_problems"] = ("Your previous reply was rejected for these reasons. "
                                           "Rewrite it so that none of them applies: " + " | ".join(feedback))
        text = json.dumps(data, ensure_ascii=False)
    result = await _call("converse", chain, s, text, Reply)
    return result.reply.strip() if result else None


async def unsupported_claims(reply: str, entry: dict, s: Settings) -> list[str] | None:
    """G14: a second, strict call lists statements not supported by the approved entry. None if it failed."""
    chain = _chain(s, verify=True)
    if not chain:
        return []  # no verifier available: rely on G13 alone
    text = json.dumps({"material": material(entry), "reply": reply}, ensure_ascii=False)
    result = await _call("verify", chain, s, text, Verdict)
    return result.unsupported if result else None
