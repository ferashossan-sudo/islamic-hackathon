"""Message pipeline (build-plan §2). Every path ends in a fixed or approved card."""
import re

from app import texts
from app.config import Settings
from app.schemas import ChatRequest, ChatResponse

MAX_CHARS = 800

# Zero-width characters, bidi controls, soft hyphen and BOM.
_INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁩﻿]")


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


def abstain(s: Settings, key: str = "abstain") -> ChatResponse:
    return ChatResponse(kind="abstain", version=s.version,
                        blocks=[message_block(key), *_suggest_block(s)])


def fail_closed(s: Settings) -> ChatResponse:
    """G12: any failure in composing or checking an answer becomes this card."""
    return ChatResponse(kind="abstain", version=s.version, blocks=[message_block("fail_closed")])


def handle(req: ChatRequest, s: Settings) -> tuple[int, ChatResponse]:
    message = clean_message(req.message)
    # Step 2, distress detection, runs here before any other check (WP3).
    if not message:
        return 422, ChatResponse(kind="abstain", version=s.version, blocks=[message_block("input_invalid")])
    if len(message) > MAX_CHARS:
        return 422, ChatResponse(kind="abstain", version=s.version, blocks=[message_block("input_too_long")])
    # No approved entries yet: the router is skipped and the fixed abstention is returned.
    return 200, abstain(s)
