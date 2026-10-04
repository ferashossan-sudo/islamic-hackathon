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
              science=[{"claim": "للكون بداية زمنية", "degree": "leading_theory", "source": "U.S. Department of Energy, DOE Explains: Cosmology",
                        "url": "https://www.energy.gov/science/doe-explainscosmology", "licence": "Public domain (U.S. Department of Energy)"}],
              hadiths=[{"text": "كل مولود يولد على الفطرة", "source": "صحيح البخاري", "number": "1358", "grade": "صحيح",
                        "grader": "البخاري ومسلم (متفق عليه)", "url": "https://hadeethenc.com/ar/browse/hadith/1",
                        "via": "hadeethenc", "purpose": "evidence"}])
NAMES = ["الدرر السنية", "U.S. Department of Energy, DOE Explains: Cosmology", "صحيح البخاري"]

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


def test_a_repeated_verse_or_hadith_is_shown_once(fakes):
    fakes["replies"] = [GOOD + " وكما في {{q:52:35}} و{{h:1}}."]
    chat = ask("هل الكون صدفة؟").blocks[0]
    assert [seg["type"] for seg in chat["segments"]].count("verse") == 1
    assert [seg["type"] for seg in chat["segments"]].count("hadith") == 1
    assert any("[الطور: 35]" in seg["text"] for seg in chat["segments"] if seg["type"] == "text")


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


def test_g13_failure_gets_one_rewrite_then_drops_without_calling_the_verifier(fakes):
    fakes["replies"] = [GOOD + " وقد أثبت العلم ذلك.", GOOD + " وقد أثبت العلم ذلك."]
    fakes["verdicts"] = []
    r = ask("هل الكون صدفة؟")
    assert r.blocks[0]["type"] != "chat"
    assert "أثبت العلم" in fakes["payloads"][1]["previous_reply_problems"]


def test_g13_rewrite_that_passes_both_checks_is_shown(fakes):
    fakes["replies"] = [GOOD.replace("{{q:52:35}}", "﴿أم خلقوا من غير شيء﴾"), GOOD]
    fakes["verdicts"] = [[]]
    assert ask("هل الكون صدفة؟").blocks[0]["type"] == "chat"
    assert "placeholders" in fakes["payloads"][1]["previous_reply_problems"]


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


@pytest.mark.parametrize("bad, code", [
    (GOOD.replace("وقال النبي ﷺ {{h:1}}.", "وقال النبي ﷺ {{h:1}} إن كل مولود يولد على الفطرة."), "verse_or_hadith_words"),
    (GOOD.replace("قال تعالى {{q:52:35}}", "فخلقوا من غير شيء كما يسأل القرآن {{q:52:35}}"), "verse_or_hadith_words"),
    (GOOD.replace("{{q:52:35}}", "{{q:52:35}} و{{q:52:36}}"), "placeholders"),
])
def test_g13_catches_restated_texts_and_walls_of_text(bad, code):
    entry = {**ENTRY, "verses": ["52:35", "52:36"]}
    assert guards.reply_problem(bad, entry, NAMES) == code


def test_g13_no_placeholders_when_the_person_asked_for_reason_only():
    assert guards.reply_problem(GOOD, ENTRY, NAMES, message="أقنعني بالعقل بدون دين") == "reason_only"
    assert guards.reply_problem(GOOD, ENTRY, NAMES, message="هل الكون صدفة؟") is None


def test_g13_a_consensus_word_the_entry_itself_uses_is_allowed():
    entry = {**ENTRY, "body": ENTRY["body"] + " وينقل العلماء الإجماع على ذلك."}
    reply = GOOD.replace("وش رأيك،", "وهذا بالإجماع كما ينقل العلماء. وش رأيك،")
    assert guards.reply_problem(reply, entry, NAMES) is None
    assert guards.reply_problem(reply, ENTRY, NAMES) == "phrase:بإجماع"


def test_a_flattering_opening_gets_one_rewrite(fakes):
    fakes["replies"] = ["سؤالك مهم ويعكس حرصك. " + GOOD, GOOD]
    fakes["verdicts"] = [[]]
    r = ask("هل الكون صدفة؟")
    assert r.blocks[0]["type"] == "chat"
    assert "praise" in fakes["payloads"][1]["previous_reply_problems"]


def test_a_push_back_whose_dialogue_is_dropped_gets_the_next_layer_not_the_same_card(fakes):
    from app.schemas import ChatContext, RecentItem
    fakes["replies"] = [GOOD + " وقد أثبت العلم ذلك."] * 3
    fakes["verdicts"] = []
    context = ChatContext(prev_entry_id="kawn-universe-x",
                          recent=[RecentItem(entry_id="kawn-universe-x", kind="answer", layer="summary")])
    req = ChatRequest(message="مو مقتنع، الكون صدفة", context=context)
    r = asyncio.run(pipeline.handle(req, SETTINGS))[1]
    assert r.layer != "summary" and r.blocks[0]["type"] != "chat"
