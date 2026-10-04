"""G3 misquoted verses, and M12 repeated questions."""
import asyncio
import copy

import pytest

from app import arabic, main, pipeline, quran
from app.schemas import ChatContext, ChatRequest, RecentItem
from tests.test_lexical_line import SAMPLE


@pytest.fixture
def loaded():
    before = (pipeline.STATE.entries, pipeline.STATE.index, pipeline.STATE.source_names)
    pipeline.load(copy.deepcopy(SAMPLE))
    yield
    pipeline.STATE.entries, pipeline.STATE.index, pipeline.STATE.source_names = before


# --- G3 ---

def test_c11_misquote_is_detected():
    found = quran.find_misquote("الآية تقول: «والسماء بنيناها بقوة وإنا لموسعون»، يعني الكون يتمدد؟")
    assert found and found.verse.ref == "51:47"


def _correct_quotes():
    picks = []
    for key in [(2, 255), (3, 190), (21, 30), (51, 47), (52, 35), (55, 26), (67, 2), (112, 1), (94, 6), (88, 17),
                (36, 82), (49, 13), (13, 28), (29, 48), (2, 186)]:
        words = quran.verses()[key].plain.split()
        picks.append("قال تعالى: «" + " ".join(words[:7]) + "»")
    return picks


@pytest.mark.parametrize("message", _correct_quotes())
def test_correct_quotes_are_not_flagged(message):
    assert quran.find_misquote(message) is None, message


@pytest.mark.parametrize("message", [
    "الله خلق السماوات والأرض في ستة أيام",
    "إن شاء الله بكرة أسأل",
    "الحمد لله على كل حال",
    "الله يقول إن مع العسر يسرا فاصبر",
    "هل الكون جاء صدفة أم خلقه الله؟",
    "سبحان الله والحمد لله",
    "القرآن يقول إن الله خلق كل شيء",
    "ربي يحب الصابرين وأنا أحاول أصبر",
    "الله أكبر",
    "لا حول ولا قوة إلا بالله",
    "كيف أعرف أن الله موجود؟",
    "ليش فيه شر في الدنيا؟",
    "قرأت أن السماء كانت ملتصقة بالأرض",
    "يقولون إن الكون يتمدد، هل في القرآن شيء عن هذا؟",
    "توكلت على الله في أمري",
])
def test_paraphrases_are_not_flagged(message):
    assert quran.find_misquote(message) is None, message


def test_misquote_notice_is_attached_to_the_response(loaded):
    _, r = asyncio.run(pipeline.handle(
        ChatRequest(message="الآية تقول: «والسماء بنيناها بقوة وإنا لموسعون»", mode="offline"), main.settings))
    assert r.blocks[0]["key"] == "misquote_notice"
    assert r.blocks[0]["ref"] == "51:47"
    assert quran.lookup("51:47")[0].text in r.blocks[0]["text"]


# --- M12 repeated question ---

def test_third_repeat_gets_summary_guidance_and_referral(loaded):
    question = "كيف أتأكد إن الله موجود؟"
    context = ChatContext()
    sent, responses = [], []
    for turn in range(1, 4):
        repeats = sum(1 for m in sent if arabic.trigram_similarity(m, question) >= 0.8)
        context = context.model_copy(update={"repeat_count": repeats})
        _, r = asyncio.run(pipeline.handle(ChatRequest(message=question, turn=turn, context=context, mode="offline"),
                                           main.settings))
        responses.append(r)
        sent.append(question)
        if r.entry_id:
            context = ChatContext(prev_entry_id=r.entry_id, repeat_count=repeats,
                                  recent=context.recent + [RecentItem(entry_id=r.entry_id, kind=r.kind, layer="summary")])
    assert responses[0].kind == "answer" and responses[1].kind == "answer"
    third = responses[2]
    keys = [b.get("key") for b in third.blocks]
    assert keys[0] == "notice_repeat" and keys[-1] == "repeat_referral"
    answer_block = next(b for b in third.blocks if b["type"] == "answer")
    assert answer_block["body"] == []


def test_trigram_similarity():
    assert arabic.trigram_similarity("كيف أتأكد إن الله موجود؟", "كيف اتاكد ان الله موجود") == 1.0
    assert arabic.trigram_similarity("هل الكون صدفة", "ليش فيه شر") < 0.2


def test_hadith_grade_questions_are_referred_but_a_request_for_a_sound_hadith_is_not():
    from app.pipeline import _asks_hadith_grade
    assert _asks_hadith_grade("حديث «اطلبوا العلم ولو بالصين» صحيح ولا ضعيف؟")
    assert _asks_hadith_grade("وش صحة حديث اطلبوا العلم ولو في الصين؟")
    assert not _asks_hadith_grade("عطني حديث صحيح يقول إن الأرض كروية")
