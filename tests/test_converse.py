"""The conversational layer: G13 (deterministic) and G14 (verifier) keep it to the approved entry."""
import asyncio
import copy
import dataclasses
import json

import pytest

from app import converse, guards, main, pipeline, router
from app.schemas import ChatRequest, HistoryItem
from tests.test_lexical_line import SAMPLE, entry

ENTRY = entry("kawn-universe-x", "هل الكون جاء صدفة؟", ["الكون صدفة؟", "هل نشأ الكون بالصدفة", "الكون جا صدفة", "وجود الكون صدفة"],
              body="يرى العلماء أن للكون بداية قبل نحو 13.8 مليار سنة، وما له بداية لا بد له من موجد {{q:52:35}}.",
              science=[{"claim": "للكون بداية زمنية", "degree": "leading_theory", "source": "OpenStax Astronomy 2e",
                        "url": "https://openstax.org/books/astronomy-2e/pages/29-3", "licence": "CC BY-NC-SA 4.0"}],
              hadiths=[{"text": "كل مولود يولد على الفطرة", "source": "صحيح البخاري", "number": "1358", "grade": "صحيح",
                        "grader": "البخاري ومسلم (متفق عليه)", "url": "https://hadeethenc.com/ar/browse/hadith/1",
                        "via": "hadeethenc", "purpose": "evidence"}])
NAMES = ["الدرر السنية", "OpenStax Astronomy 2e", "صحيح البخاري"]

GOOD = ("سؤالك في محله وكثير يسأله. العلم اليوم يرجّح أن للكون بداية زمنية، وهذه نظرية راجحة، "
        "قبل نحو 13.8 مليار سنة.\nوالعقل يقول إن ما له بداية لا بد له من موجد، قال تعالى {{q:52:35}}. "
        "وقال النبي ﷺ {{h:1}}.\nوش رأيك، هل يمكن لشيء يبدأ أن يوجد نفسه؟")


def test_grounded_reply_passes():
    assert guards.check_reply(GOOD, ENTRY, NAMES)


@pytest.mark.parametrize("bad", [
    GOOD.replace("13.8", "15"),  # a number not in the entry
    GOOD + " وقد قال ابن تيمية مثل هذا.",  # a scholar not in the entry
    GOOD.replace("{{q:52:35}}", "﴿أم خلقوا من غير شيء﴾"),  # verse text written by the model
    GOOD.replace("{{q:52:35}}", "أم خلقوا من غير شيء أم هم الخالقون"),  # verse words without brackets
    GOOD.replace("{{q:52:35}}", "{{q:2:255}}"),  # a verse not in the entry
    GOOD.replace("{{h:1}}", "{{h:2}}"),  # a hadith index that does not exist
    GOOD.replace("وقال النبي ﷺ {{h:1}}.", "وقال النبي ﷺ إن كل مولود يولد على الفطرة."),  # hadith words, no placeholder
    GOOD + " وقد أثبت العلم ذلك.",  # guarded claim
    GOOD + " وهذا بإجماع العلماء.",
    GOOD + " وهذا حرام.",
    GOOD + " كما في Wikipedia.",  # a Latin source name not in the entry
    "قصير",  # too short
])
def test_ungrounded_replies_fail(bad):
    assert not guards.check_reply(bad, ENTRY, NAMES), bad


SETTINGS = dataclasses.replace(main.settings, llm_enabled=True, router_provider="gemini", gemini_api_key="k",
                               converse_enabled=True)


@pytest.fixture
def fakes(monkeypatch):
    before = (pipeline.STATE.entries, pipeline.STATE.index, pipeline.STATE.source_names)
    pipeline.load(copy.deepcopy(SAMPLE) + [copy.deepcopy(ENTRY)])
    state = {"replies": [GOOD], "verdicts": [[]], "payloads": []}

    async def fake_router(s, system, payload, timeout):
        return json.dumps({"route": "knowledge", "entry_id": "kawn-universe-x", "confidence": "high",
                           "oos_reason": "none", "evidence_request": "none", "framing": ""}), {}

    async def fake_compose(s, payload, timeout):
        state["payloads"].append(json.loads(payload))
        return json.dumps({"reply": state["replies"].pop(0)}, ensure_ascii=False), {}

    async def fake_verify(s, payload, timeout):
        return json.dumps({"unsupported": state["verdicts"].pop(0)}, ensure_ascii=False), {}

    monkeypatch.setitem(router.PROVIDERS, "gemini", fake_router)
    monkeypatch.setitem(converse.PROVIDERS, "gemini", fake_compose)
    monkeypatch.setitem(converse.VERIFIERS, "gemini", fake_verify)
    yield state
    pipeline.STATE.entries, pipeline.STATE.index, pipeline.STATE.source_names = before


def ask(message, history=()):
    req = ChatRequest(message=message, history=[HistoryItem(**h) for h in history])
    return asyncio.run(pipeline.handle(req, SETTINGS))[1]


def test_chat_block_comes_first_with_filled_verse_and_hadith(fakes):
    r = ask("هل الكون صدفة؟")
    chat = r.blocks[0]
    assert chat["type"] == "chat"
    types = [seg["type"] for seg in chat["segments"]]
    assert "verse" in types and "hadith" in types
    hadith = next(seg for seg in chat["segments"] if seg["type"] == "hadith")
    assert hadith["text"] == "كل مولود يولد على الفطرة"
    assert "[الطور: 35]" in chat["history_text"]
    assert any(b["type"] == "sources" for b in r.blocks)  # the approved card is still there


def test_unsupported_claim_gets_one_retry_then_drops(fakes):
    fakes["replies"] = [GOOD, GOOD]
    fakes["verdicts"] = [["ادعاء زائد"], ["ادعاء زائد"]]
    r = ask("هل الكون صدفة؟")
    assert r.kind == "answer" and r.blocks[0]["type"] != "chat"
    assert "previous_reply_problems" in fakes["payloads"][1]


def test_retry_that_fixes_the_claim_is_shown(fakes):
    fakes["replies"] = [GOOD, GOOD]
    fakes["verdicts"] = [["ادعاء زائد"], []]
    assert ask("هل الكون صدفة؟").blocks[0]["type"] == "chat"


def test_g13_failure_drops_without_calling_the_verifier(fakes):
    fakes["replies"] = [GOOD + " وقد أثبت العلم ذلك."]
    fakes["verdicts"] = []
    r = ask("هل الكون صدفة؟")
    assert r.blocks[0]["type"] != "chat"


def test_history_is_passed_and_trimmed(fakes):
    history = [{"role": "user", "text": "س" * 3000 if False else "سؤال سابق"}, {"role": "assistant", "text": "جواب سابق"}]
    ask("طيب وبعدين؟", history)
    sent = fakes["payloads"][0]
    assert [h["role"] for h in sent["history"]] == ["user", "assistant"]
    assert "material" in sent and sent["material"]["question"] == "هل الكون جاء صدفة؟"


def test_no_chat_in_degraded_mode(fakes):
    req = ChatRequest(message="هل الكون جاء صدفة؟", mode="offline")
    r = asyncio.run(pipeline.handle(req, SETTINGS))[1]
    assert r.degraded and not any(b["type"] == "chat" for b in r.blocks)
