"""Message pipeline (build-plan §2). Every path ends in a fixed text or an approved entry."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app import arabic, compose, converse, distress, guards, kb, quran, router, texts
from app.config import Settings
from app.limits import LIMITER
from app.lexical import LexicalIndex
from app.schemas import ChatRequest, ChatResponse
from app.usage import log_event

MAX_CHARS = 800
NON_ARABIC_BELOW = 0.3
LEX_T_HI = 0.55  # degraded mode: answer only above this score...
LEX_MARGIN = 0.10  # ...and this far ahead of the second entry (tuned on dev on Monday)
RULING_WORDS = ("حكم", "يجوز", "حلال", "حرام", "فتوي", "طلاق")

# Zero-width characters, bidi controls, soft hyphen and BOM.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_SALAM = ("السلام عليكم", "السلام عليكم ورحمه الله", "السلام عليكم ورحمه الله وبركاته", "سلام عليكم", "السلام")
_GREETINGS = ("مرحبا", "هلا", "اهلا", "هلا والله", "صباح الخير", "مساء الخير", "hi", "hello")
_THANKS = ("شكرا", "شكرا لك", "جزاك الله خير", "جزاك الله خيرا", "الله يعطيك العافيه", "مشكور", "يعطيك العافيه")


@dataclass
class KBState:
    entries: dict[str, dict] = field(default_factory=dict)
    index: LexicalIndex | None = None
    source_names: list[str] = field(default_factory=list)  # G5 condition 5


STATE = KBState()


def load(approved: list[dict]) -> None:
    STATE.entries = {e["id"]: e for e in approved}
    STATE.index = LexicalIndex(approved) if approved else None
    names = set()
    for e in approved:
        names.add(e["source"]["name"])
        names.update(t["mufassir"] for t in e.get("tafsir", []))
        names.update(t["source"] for t in e.get("tafsir", []))
        names.update(h["source"] for h in e.get("hadiths", []))
        names.update(sc["source"] for sc in e.get("science", []))
    STATE.source_names = sorted(n for n in names if n)


def framing_block(framing: str, message: str, entry: dict, s: Settings) -> list[dict]:
    """G5: the model's linking sentence is shown only if it passes all eight conditions; else dropped."""
    if not s.framing_enabled or not framing:
        return []
    ok = guards.check_framing(framing, message, STATE.source_names, [h["text"] for h in entry.get("hadiths", [])])
    log_event(event="framing", ok=ok)
    if not ok:
        return []
    return [{"type": "framing", "label": texts.text("badge_framing"), "hint": texts.text("badge_framing_hint"),
             "text": framing.strip()}]


def clean_message(raw: str) -> str:
    return _INVISIBLE.sub("", raw).strip()


_FROM_MEMORY = re.compile(r"من حفظك|من راسك|من رأسك|من ذاكرتك|بدون (?:روابط|بطاقات|بطاقه|بطاقة)")
_EXACT_WORDS = re.compile(r"بالضبط|بنصه|بنصها|بالحرف|حرفيا|كلامه هو|مو تلخيص|ليس تلخيص")


def request_notices(message: str, response: ChatResponse) -> list[dict]:
    """A fixed line when the person asks for something the service never does, so the card does not seem to
    ignore them: texts written from memory, a scholar's exact words, or a scholar the answer does not quote."""
    blocks = []
    if _FROM_MEMORY.search(message):
        blocks.append(message_block("recite_from_source"))
    entry = STATE.entries.get(response.entry_id or "")
    if entry and _EXACT_WORDS.search(message):
        blocks.append(message_block("paraphrase_notice"))
    if entry:
        material = " ".join([*(str(entry.get(f) or "") for f in ("summary", "body", "explain_simple")),
                             *kb.reasoning_texts(entry)])
        asked = [n for n in guards.KNOWN_NAMES if n in message and n not in material]
        if asked:
            blocks.append({"type": "message", "key": "scholar_not_in_entry",
                           "text": texts.text("scholar_not_in_entry").replace("{الاسم}", asked[0])})
    return blocks


def message_block(key: str) -> dict:
    return {"type": "message", "key": key, "text": texts.text(key)}


def _suggest_block(s: Settings) -> list[dict]:
    if not s.suggest_form_url:
        return []
    labels = texts.pairs("ui_labels")
    return [{"type": "referral", "key": "suggest_question", "label": labels["suggest_button"],
             "text": texts.text("suggest_question_short"), "url": s.suggest_form_url}]


def abstain(s: Settings, key: str = "abstain", degraded: bool = False) -> ChatResponse:
    return ChatResponse(kind="abstain", version=s.version, degraded=degraded,
                        blocks=[message_block(key), *_suggest_block(s)])


def fail_closed(s: Settings) -> ChatResponse:
    """G12: any failure in composing or checking an answer becomes this card."""
    return ChatResponse(kind="abstain", version=s.version, blocks=[message_block("fail_closed")])


def distress_response(message: str, s: Settings) -> ChatResponse:
    """G7: the fixed distress card. No model call, nothing logged but the kind."""
    english = arabic.arabic_ratio(message) < NON_ARABIC_BELOW
    text = texts.text_en("distress") if english else texts.text("distress")
    before, _, after = text.partition("{أرقام الدعم}")
    blocks = [{"type": "message", "key": "distress", "text": before.strip()},
              {"type": "contacts", "items": distress.contacts()}]
    if distress.mentions_harm_by_others(message):
        key = "distress_addon_harm"
        blocks.append({"type": "message", "key": key, "text": texts.text_en(key) if english else texts.text(key)})
    if after.strip():
        blocks.append({"type": "message", "key": "distress_after", "text": after.strip()})
    return ChatResponse(kind="distress", version=s.version, blocks=blocks)


GLOSSARY_PATH = Path(__file__).resolve().parent.parent / "content" / "glossary.json"


def load_glossary() -> list[dict]:
    if not GLOSSARY_PATH.exists():
        return []
    return json.loads(GLOSSARY_PATH.read_text(encoding="utf-8")).get("terms", [])


def glossary_block(message: str) -> list[dict]:
    """R6 (translation and localisation): the approved English meaning of a religious term the person used."""
    words = set(re.findall(r"[a-z']+", message.lower().replace("’", "'")))
    items = [{"term_en": t["term_en"], "term_ar": t["term_ar"], "definition_en": t["definition_en"], "url": t["url"]}
             for t in load_glossary() if words & set(t["match"])]
    if not items:
        return []
    return [{"type": "glossary", "title": "From the Encyclopedia of Translated Islamic Terms", "items": items[:3]}]


def non_arabic_response(s: Settings, message: str = "") -> ChatResponse:
    return ChatResponse(kind="non_arabic", version=s.version, blocks=[*glossary_block(message),
        {"type": "message", "key": "non_arabic", "text": texts.text_en("non_arabic"), "lang": "en"},
        {"type": "message", "key": "non_arabic", "text": texts.text("non_arabic")},
        {"type": "message", "key": "support_line", "text": texts.text("support_line")},
    ])


def smalltalk(message: str, s: Settings) -> ChatResponse | None:
    words = " ".join(arabic.words(message))
    for key, phrases in (("greeting_salam", _SALAM), ("greeting_general", _GREETINGS), ("thanks", _THANKS)):
        if words in {arabic.normalize(p) for p in phrases}:
            return ChatResponse(kind="smalltalk", version=s.version, blocks=[message_block(key)])
    return None


def misquote_block(message: str) -> list[dict]:
    """G3: a verse quoted with a mistake gets a gentle notice with the mushaf text."""
    found = quran.find_misquote(message)
    if found is None:
        return []
    v = found.verse
    text = (texts.text("misquote_notice").replace("{النص}", v.text)
            .replace("{السورة}", quran.sura_name(v.sura)).replace("{رقم الآية}", str(v.aya)))
    return [{"type": "notice", "key": "misquote_notice", "text": text, "ref": v.ref}]


def repeat_response(entry_id: str, s: Settings, degraded: bool) -> ChatResponse:
    """M12, C-22: the same question a third time gets the summary, the approved guidance and a referral."""
    entry = STATE.entries[entry_id]
    blocks = [message_block("notice_repeat")]
    answer_block = next(b for b in compose.answer_blocks(entry, STATE.entries) if b["type"] == "answer")
    blocks.append({**answer_block, "body": [], "explain_simple": None})
    guidance = STATE.entries.get("tasawur-waswasa")
    if guidance and entry_id != "tasawur-waswasa":
        blocks.append({"type": "guidance", "entry_id": guidance["id"], "summary": compose.segments(guidance["summary"])})
    blocks.append(message_block("repeat_referral"))
    return ChatResponse(kind="answer", entry_id=entry_id, layer="summary", degraded=degraded, version=s.version,
                        blocks=blocks)


def answered_before(entry_id: str, req: ChatRequest) -> bool:
    return any(item.entry_id == entry_id and item.kind == "answer" for item in req.context.recent)


def answer(entry_id: str, s: Settings, degraded: bool, layer: str = "summary",
           framing: list[dict] | None = None) -> ChatResponse:
    entry = STATE.entries[entry_id]
    blocks = (framing or []) + compose.answer_blocks(entry, STATE.entries, layer)
    return ChatResponse(kind="answer", entry_id=entry_id, layer=layer, degraded=degraded, version=s.version,
                        blocks=blocks)


def lexical_decision(message: str, s: Settings, req: ChatRequest | None = None) -> ChatResponse:
    """Degraded mode (build-plan §2 decision table, last row)."""
    hits = STATE.index.search(message) if STATE.index else []
    top_score = hits[0][1] if hits else 0.0
    second = hits[1][1] if len(hits) > 1 else 0.0
    if hits and top_score >= LEX_T_HI and top_score - second >= LEX_MARGIN:
        if req is not None and req.context.repeat_count >= 2 and answered_before(hits[0][0], req):
            return repeat_response(hits[0][0], s, degraded=True)
        return answer(hits[0][0], s, degraded=True)
    stems = set().union(*(guards._stems(w) for w in arabic.words(message))) if message else set()
    if stems & {arabic.normalize(w) for w in RULING_WORDS}:
        return ChatResponse(kind="refer", version=s.version, degraded=True, blocks=[message_block("referral_fiqh")])
    return abstain(s, degraded=True)


REFERRALS = {"personal_fatwa": "referral_personal_fatwa", "fiqh": "referral_fiqh",
             "hadith_check": "referral_hadith_check", "family_faith": "referral_family_faith",
             "judging_groups": "referral_judging_groups",
             "other_topic": "referral_other_topic", "manipulation": "referral_manipulation",
             "none": "referral_other_topic"}
_HADITH_GRADE = re.compile(r"حديث")
# Asking for a grade («صحيح ولا ضعيف؟»، «وش صحته؟»), not asking for a sound hadith («عطني حديث صحيح»).
_GRADE_WORDS = re.compile(r"(?:صحيح|ضعيف|موضوع|ثابت)\s*(?:ولا|او|أو|؟|\?)|(?:وش|ما|مدى)\s+صح[ةه]|صحت[هه]|"
                          r"درجت[هه]|درج[ةه]\s+(?:الحديث|هذا)|يصح\s|مكذوب")
_SOURCE_ASK = re.compile(r"مصدر|المصدر|مرجع|من وين|منين|وين لقيت|جايب")


def _asks_hadith_grade(message: str) -> bool:
    return bool(_HADITH_GRADE.search(message) and _GRADE_WORDS.search(message))


MAYBE_MIN_SCORE = 0.2


def _maybe_block(message: str, exclude: str | None = None) -> list[dict]:
    """W2 «ربما تقصد»: up to three approved questions that are lexically close."""
    hits = STATE.index.search(message, k=4) if STATE.index else []
    items = [{"id": eid, "question": compose.question_text(STATE.entries[eid]["question"])}
             for eid, score in hits if score >= MAYBE_MIN_SCORE and eid != exclude][:3]
    if not items:
        return []
    return [{"type": "related", "title": texts.pairs("ui_labels")["maybe_you_mean"], "items": items}]


def _followup(prev: dict, req: ChatRequest, s: Settings) -> ChatResponse:
    """M12: the simple explanation first (if any), then the full answer, then the exhaustion text."""
    shown = {item.layer for item in req.context.recent if item.entry_id == prev["id"]}
    if prev.get("explain_simple") and "explain" not in shown and "body" not in shown:
        return answer(prev["id"], s, degraded=False, layer="explain")
    if "body" not in shown:
        return answer(prev["id"], s, degraded=False, layer="body")
    blocks = [message_block("followup_exhausted"),
              {"type": "sources", "items": [{"name": prev["source"]["name"], "locator": prev["source"].get("locator", ""),
                                             "url": prev["source"]["url"]}]}]
    return ChatResponse(kind="refer", entry_id=prev["id"], version=s.version, blocks=blocks)


def model_decision(decision, message: str, prev: dict | None, req: ChatRequest, s: Settings) -> ChatResponse:
    """Build-plan §2 decision table, read top to bottom."""
    if decision.route == "distress":
        return distress_response(message, s)
    if decision.route == "out_of_scope":
        if decision.oos_reason == "manipulation" and prev is not None and _SOURCE_ASK.search(message):
            return _followup(prev, req, s)  # «وش المصدر اللي جايب منه هالكلام؟» asks for the sources, not a role
        key = REFERRALS[decision.oos_reason]
        extra = _suggest_block(s) if key == "referral_other_topic" else []
        return ChatResponse(kind="refer", version=s.version, blocks=[message_block(key), *extra])
    entry = STATE.entries.get(decision.entry_id)
    # The repeat rule comes before follow-up (decision table row 3): the third time, whatever the route.
    resolved = prev if decision.route == "followup" and prev is not None else entry
    if req.context.repeat_count >= 2 and resolved is not None and answered_before(resolved["id"], req):
        return repeat_response(resolved["id"], s, degraded=False)
    if decision.route == "followup" and prev is not None:
        return _followup(prev, req, s)
    if entry is None and _asks_hadith_grade(message):
        return ChatResponse(kind="refer", version=s.version, blocks=[message_block("referral_hadith_check")])
    if decision.evidence_request != "none":
        has = bool(entry and (entry.get("hadiths") if decision.evidence_request == "hadith" else entry.get("verses")))
        if not has:
            blocks = [message_block("no_matching_evidence")]
            if decision.evidence_request == "hadith":
                blocks.append(message_block("no_matching_evidence_hadith"))
            if entry:
                blocks.append({"type": "related", "title": texts.pairs("ui_labels")["may_help"],
                               "items": [{"id": entry["id"], "question": compose.question_text(entry["question"])}]})
            return ChatResponse(kind="abstain", version=s.version, blocks=blocks)
    if entry is not None:
        top3 = [eid for eid, _ in (STATE.index.search(message, k=3) if STATE.index else [])]
        accepted = decision.confidence == "high" or (
            decision.confidence == "medium" and s.confidence_min != "high" and entry["id"] in top3)
        if accepted:
            return answer(entry["id"], s, degraded=False,
                          framing=framing_block(decision.framing, message, entry, s))
    return ChatResponse(kind="abstain", version=s.version,
                        blocks=[message_block("abstain"), *_maybe_block(message), *_suggest_block(s)])


def limit_response(wait_seconds: int, s: Settings) -> ChatResponse:
    minutes = max(1, round(wait_seconds / 60))
    duration = "دقيقة واحدة" if minutes == 1 else ("دقيقتين" if minutes == 2 else f"{minutes} دقائق")
    return ChatResponse(kind="limit", version=s.version, blocks=[
        {"type": "message", "key": "rate_limit", "text": texts.text("rate_limit").replace("{المدة}", duration)},
        message_block("support_line")])


async def handle(req: ChatRequest, s: Settings, address: str = "") -> tuple[int, ChatResponse]:
    message = clean_message(req.message)
    # Step 2: distress first, on the whole message, before validation, limits, language or any model.
    if distress.detect_distress(message):
        return 200, distress_response(message, s)
    if not message:
        return 422, ChatResponse(kind="abstain", version=s.version, blocks=[message_block("input_invalid")])
    if len(message) > MAX_CHARS:
        return 422, ChatResponse(kind="abstain", version=s.version, blocks=[message_block("input_too_long")])
    # Step 3: rate limits, after distress so a person in distress always gets the support card.
    if (wait := LIMITER.check(req.sid, address)) > 0:
        return 429, limit_response(wait, s)
    if (reply := smalltalk(message, s)) is not None:
        return 200, reply
    if arabic.arabic_ratio(message) < NON_ARABIC_BELOW:
        return 200, non_arabic_response(s, message)
    notice = misquote_block(message)
    response = await _respond(message, req, s)
    if response.kind not in ("distress", "non_arabic", "smalltalk"):
        response.blocks = notice + request_notices(message, response) + response.blocks
    return 200, response


def _history_text(reply: str, entry: dict) -> str:
    """The reply as plain text for the browser's short history (placeholders become references)."""
    def sub(match):
        kind, value = match.groups()
        return f"[{quran.label(value)}]" if kind == "q" else f"[حديث {value}]"
    return compose.CHAT_PLACEHOLDER.sub(sub, reply)


G13_FEEDBACK = {
    "length": "The reply was too short or too long. Keep it to a few short paragraphs.",
    "brackets": "Do not write verse brackets or quote verses. Use only the {{q:...}} placeholders from the material.",
    "verse_placeholder": "You used a verse reference that is not in the material. Use only the material's verses.",
    "hadith_placeholder": "You used a hadith number that is not in the material. Use only {{h:n}} from the material.",
    "verse_or_hadith_words": "You wrote words of a verse or hadith yourself. Refer to them only with the "
                             "{{q:...}} or {{h:n}} placeholders, and say what they show in your own words BEFORE "
                             "the placeholder; never restate them after it.",
    "placeholders": "Too many verses and hadiths: use at most two placeholders, and at most one hadith.",
    "attribution": "This is a contested answer: every sentence about a ruling, a penalty, a permission or a "
                   "consensus must name who holds it, from the material, in the same sentence (for example "
                   "«تذكر الموسوعة الفقهية في الدرر السنية أن...»). Never say «العلماء» or «اتفق» in your own voice.",
    "restated": "Do not restate a verse or hadith after its placeholder: say what it shows in your own words "
                "BEFORE the placeholder, and let the placeholder end the sentence.",
    "reason_only": "The person asked to be convinced by reason only: use no verse or hadith placeholder; you may "
                   "say in one short line that the answer also has its sharia evidence for whoever wants it.",
    "prophet_mention": "Do not attribute anything to the Prophet ﷺ unless you use an {{h:n}} placeholder.",
    "prophet_words": "Do not quote the Prophet ﷺ in your own words. Use only an {{h:n}} placeholder.",
    "number": "You wrote a number that is not in the material. Remove it.",
    "latin": "You wrote a Latin word or name that is not in the material. Remove it.",
}


def g13_feedback(problem: str) -> str:
    if problem.startswith("phrase:"):
        return f"Do not use «{problem.split(':', 1)[1]}» or any name or claim that the material does not contain."
    return G13_FEEDBACK.get(problem, "Keep strictly to the material.")


def _quoted(history: list[dict], entry: dict) -> frozenset:
    """Verses and hadiths of this entry already shown in the conversation (from the history text)."""
    shown = " ".join(h["text"] for h in history if h["role"] == "assistant")
    verses = {("q", ref) for ref in entry.get("verses", []) if f"[{quran.label(ref)}]" in shown}
    hadiths = {("h", str(i)) for i in range(1, len(entry.get("hadiths", [])) + 1) if f"[حديث {i}]" in shown}
    return frozenset(verses | hadiths)


# Soft style checks: one rewrite, never a reason to drop a reply that passed G13 and G14.
FLATTERY = ("سؤالك مهم", "سؤال مهم جدا", "يعكس", "ينم عن", "يدل على حرصك", "أقدر حرصك", "تفكيرك النقدي",
            "دليل على تفكيرك", "حرصك على")
STYLE_FEEDBACK = {
    "flattery": "Do not praise the person or describe their thinking or motives; open with one plain sentence "
                "about the question itself.",
    "repeated": "You repeated your previous reply. The person was not convinced: bring a different point from the "
                "material, or say honestly that this approved answer has nothing more on that point.",
    "opens_with_placeholder": "Do not open with a verse or hadith: start with one plain sentence about the question.",
    "no_paragraphs": "Write short paragraphs separated by \\n.",
    "register": "The person writes in Gulf dialect: answer in light, polite Gulf dialect like a respected friend.",
}
GULF_MARKERS = frozenset(arabic.normalize(w) for w in (
    "وش", "ليش", "ابي", "أبي", "ابغى", "عطني", "قريت", "طيب", "يعني", "مو", "ولا لا", "شلون", "وشلون", "كذا", "زين"))


def style_problem(reply: str, history: list[dict], message: str = "") -> str | None:
    opening = arabic.normalize(reply[:140])
    if any(arabic.normalize(p) in opening for p in FLATTERY):
        return "flattery"
    if "{{" in reply[:30]:
        return "opens_with_placeholder"
    last = next((h["text"] for h in reversed(history) if h["role"] == "assistant"), "")
    if last and arabic.trigram_similarity(last, reply) >= 0.5:
        return "repeated"
    if len(reply.split()) > 80 and "\n" not in reply.strip():
        return "no_paragraphs"
    if set(arabic.words(message)) & GULF_MARKERS and not set(arabic.words(reply)) & GULF_MARKERS:
        return "register"
    return None


async def attach_chat(response: ChatResponse, message: str, req: ChatRequest, s: Settings) -> ChatResponse:
    """The conversational layer over an approved answer (G13, G14). On any failure the card stands alone."""
    if response.kind != "answer" or response.degraded or not response.entry_id:
        return response
    if any(b.get("key") == "notice_repeat" for b in response.blocks):
        return response
    entry = STATE.entries[response.entry_id]
    if entry.get("chat") == "card_only":  # the reviewer chose the approved card alone for this answer
        return response
    history = [h.model_dump() for h in req.history]
    reply, feedback, ok, styled, verified = None, None, False, False, 0
    for attempt in range(1, 4):  # at most three replies and two verifier calls
        reply = await converse.compose_reply(message, history, entry, s, feedback)
        problem = "no_reply" if reply is None else guards.reply_problem(reply, entry, STATE.source_names, message)
        if problem:
            log_event(event="g13", reason=problem)
            if problem == "no_reply" or attempt == 3:
                break
            feedback = [g13_feedback(problem)]
            continue
        style = None if styled or attempt == 3 else style_problem(reply, history, message)
        if style:
            styled = True
            log_event(event="g13", reason="style:" + style)
            feedback = [STYLE_FEEDBACK[style]]
            continue
        if verified == 2:
            break
        verified += 1
        unsupported = await converse.unsupported_claims(reply, entry, s)  # G14
        if unsupported == []:
            ok = True
            break
        if unsupported is None:
            break
        feedback = [f"A statement the material does not support: {claim}" for claim in unsupported]
    log_event(event="converse", ok=ok)
    if not ok:
        if response.layer == "summary" and answered_before(entry["id"], req):
            return _followup(entry, req, s)  # a push-back answered with the same card again would feel like a wall
        return response
    chat = {"type": "chat", "label": texts.text("badge_chat"), "hint": texts.text("badge_chat_hint"),
            "toggle": texts.text("chat_card_toggle"),
            "segments": compose.chat_segments(reply, entry, _quoted(history, entry)),
            "history_text": _history_text(reply, entry)}
    compose.final_check([chat], entry)
    response.blocks = [chat] + [b for b in response.blocks if b["type"] != "framing"]
    return response


async def _respond(message: str, req: ChatRequest, s: Settings) -> ChatResponse:
    if not STATE.entries:
        # No approved entries yet: the router is skipped and the fixed abstention is returned.
        return abstain(s)
    if s.llm_enabled and req.mode != "offline" and LIMITER.llm_allowed():
        prev = STATE.entries.get(req.context.prev_entry_id or "")
        prev_message = next((h.text for h in reversed(req.history) if h.role == "user"), "")
        decision = await router.decide(message, prev, list(STATE.entries.values()), s, prev_message)
        if decision is not None:
            return await attach_chat(model_decision(decision, message, prev, req, s), message, req, s)
    # Degraded mode: model disabled, offline mode requested, or the call failed.
    return lexical_decision(message, s, req)
