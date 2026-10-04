"""Message pipeline (build-plan §2). Every path ends in a fixed text or an approved entry."""
import re
from dataclasses import dataclass, field

from app import arabic, compose, distress, texts
from app.config import Settings
from app.lexical import LexicalIndex
from app.schemas import ChatRequest, ChatResponse

MAX_CHARS = 800
NON_ARABIC_BELOW = 0.3
LEX_T_HI = 0.55  # degraded mode: answer only above this score...
LEX_MARGIN = 0.10  # ...and this far ahead of the second entry (tuned on dev on Monday)
RULING_WORDS = ("حكم", "يجوز", "حلال", "حرام", "فتوي", "طلاق")

# Zero-width characters, bidi controls, soft hyphen and BOM.
_INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_SALAM = ("السلام عليكم", "السلام عليكم ورحمه الله", "السلام عليكم ورحمه الله وبركاته", "سلام عليكم", "السلام")
_GREETINGS = ("مرحبا", "هلا", "اهلا", "هلا والله", "صباح الخير", "مساء الخير", "hi", "hello")
_THANKS = ("شكرا", "شكرا لك", "جزاك الله خير", "جزاك الله خيرا", "الله يعطيك العافيه", "مشكور", "يعطيك العافيه")


@dataclass
class KBState:
    entries: dict[str, dict] = field(default_factory=dict)
    index: LexicalIndex | None = None


STATE = KBState()


def load(approved: list[dict]) -> None:
    STATE.entries = {e["id"]: e for e in approved}
    STATE.index = LexicalIndex(approved) if approved else None


def clean_message(raw: str) -> str:
    return _INVISIBLE.sub("", raw).strip()


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


def non_arabic_response(s: Settings) -> ChatResponse:
    return ChatResponse(kind="non_arabic", version=s.version, blocks=[
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


def answer(entry_id: str, s: Settings, degraded: bool, layer: str = "summary") -> ChatResponse:
    entry = STATE.entries[entry_id]
    blocks = compose.answer_blocks(entry, STATE.entries, layer)
    return ChatResponse(kind="answer", entry_id=entry_id, layer=layer, degraded=degraded, version=s.version,
                        blocks=blocks)


def lexical_decision(message: str, s: Settings) -> ChatResponse:
    """Degraded mode (build-plan §2 decision table, last row)."""
    hits = STATE.index.search(message) if STATE.index else []
    top_score = hits[0][1] if hits else 0.0
    second = hits[1][1] if len(hits) > 1 else 0.0
    if hits and top_score >= LEX_T_HI and top_score - second >= LEX_MARGIN:
        return answer(hits[0][0], s, degraded=True)
    if set(arabic.words(message)) & {arabic.normalize(w) for w in RULING_WORDS}:
        return ChatResponse(kind="refer", version=s.version, degraded=True, blocks=[message_block("referral_fiqh")])
    return abstain(s, degraded=True)


def handle(req: ChatRequest, s: Settings) -> tuple[int, ChatResponse]:
    message = clean_message(req.message)
    # Step 2: distress first, on the whole message, before validation, limits, language or any model.
    if distress.detect_distress(message):
        return 200, distress_response(message, s)
    if not message:
        return 422, ChatResponse(kind="abstain", version=s.version, blocks=[message_block("input_invalid")])
    if len(message) > MAX_CHARS:
        return 422, ChatResponse(kind="abstain", version=s.version, blocks=[message_block("input_too_long")])
    if (reply := smalltalk(message, s)) is not None:
        return 200, reply
    if arabic.arabic_ratio(message) < NON_ARABIC_BELOW:
        return 200, non_arabic_response(s)
    if not STATE.entries:
        # No approved entries yet: the router is skipped and the fixed abstention is returned.
        return 200, abstain(s)
    # The router (one model call) goes here in WP4; until then, and whenever it is unavailable,
    # the lexical scorer decides alone.
    return 200, lexical_decision(message, s)
