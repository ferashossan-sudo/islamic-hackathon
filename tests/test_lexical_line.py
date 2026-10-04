"""WP3: distress first (G7), language, small talk, lexical retrieval, answer composition (step 11)."""
import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

from app import compose, distress, kb, main, pipeline
from app.schemas import ChatRequest

client = TestClient(main.app)


def entry(entry_id, question, variants, level="B", **extra):
    e = {
        "id": entry_id, "theme": entry_id.split("-")[0], "level": level, "status": "draft",
        "question": question, "variants": variants,
        "summary": "خلاصة " + question, "body": "نص فيه {{q:52:35}} للاختبار.", "explain_simple": "شرح مبسط.",
        "transfer": "paraphrase", "source": {"name": "الدرر السنية", "url": "https://dorar.net/aqeeda/1", "locator": ""},
        "verses": ["52:35"], "tafsir": [], "hadiths": [], "science": [], "related": [],
        "featured": False, "prepared_by": "فراس", "drafted_with_ai": True,
    }
    e.update(extra)
    e = kb.normalize_refs(e)
    e["status"] = "approved"
    e["review"] = {"reviewer": "المراجع الشرعي للفريق", "reviewed_at": "2026-10-04", "note": "",
                   "approved_hash": kb.approved_hash(e)}
    assert kb.validate_entry(e)[0] == []
    return e


SAMPLE = [
    entry("kawn-god-existence", "كيف أعرف أن الله موجود؟",
          ["ما الدليل على وجود الله؟", "وش الدليل إن الله موجود؟", "كيف أتأكد إن الله موجود؟", "هل الله موجود فعلاً؟"]),
    entry("kawn-universe-chance", "هل الكون جاء صدفة؟",
          ["هل الكون نشأ بالصدفة؟", "الكون جا صدفة؟", "هل وجود الكون مجرد صدفة؟", "الكون خلق نفسه بالصدفة"], level="C",
          related=["kawn-god-existence"]),
    entry("sharr-why-evil", "لماذا يوجد الشر والمعاناة في العالم؟",
          ["ليش فيه شر في الدنيا؟", "لماذا يسمح الله بالألم؟", "ليش ربي يخلي الناس تتعذب؟", "الحكمة من وجود الشر"]),
    entry("tasawur-kaaba", "هل يعبد المسلمون الكعبة؟",
          ["ليش المسلمين يعبدون الكعبة؟", "المسلمين يسجدون للكعبة؟", "لماذا يتجه المسلمون للكعبة؟"]),
]


@pytest.fixture
def loaded():
    before = (pipeline.STATE.entries, pipeline.STATE.index)
    pipeline.load(copy.deepcopy(SAMPLE))
    yield
    pipeline.STATE.entries, pipeline.STATE.index = before


def ask(message, mode=""):
    status, response = asyncio.run(pipeline.handle(ChatRequest(message=message, mode=mode), main.settings))
    return status, response


# --- G7 distress ---

@pytest.mark.parametrize("message", [
    "تعبت من كل شي، وما عاد أبي أعيش",  # C-13
    "إذا الله موجود ليش ما يساعدني؟ أفكر أنهي كل شي",  # C-14
    "أفكر في الانتحار",
    "والله ودي أموت وأرتاح",
    "ابي اذبح نفسي",
    "I want to kill myself",
])
def test_distress_detected(message):
    assert distress.detect_distress(message), message
    status, r = ask(message)
    assert (status, r.kind) == (200, "distress")
    assert any(b["type"] == "contacts" for b in r.blocks)


@pytest.mark.parametrize("message", ["ما معنى الحياة؟", "ليش نموت؟", "ما الحكمة من الموت؟", "هل الكون جاء صدفة؟"])
def test_accepted_false_alarms_do_not_trigger(message):
    assert not distress.detect_distress(message)


def test_distress_runs_before_length_check():
    status, r = ask("أفكر في الانتحار " + "ك" * 900)
    assert (status, r.kind) == (200, "distress")


def test_distress_works_with_llm_disabled_and_offline(loaded):
    assert ask("ما عاد أبي أعيش", mode="offline")[1].kind == "distress"


def test_harm_by_others_adds_1919_line():
    r = ask("أبوي يضربني وما عاد أبي أعيش")[1]
    assert any(b.get("key") == "distress_addon_harm" for b in r.blocks)
    r = ask("ما عاد أبي أعيش")[1]
    assert not any(b.get("key") == "distress_addon_harm" for b in r.blocks)


def test_contacts_come_from_approved_text():
    numbers = [c["number"] for c in distress.contacts()]
    assert "911" in numbers and "937" in numbers


# --- language and small talk ---

def test_non_arabic_gets_bilingual_referral():
    status, r = ask("What does jihad mean in Islam?")  # C-12
    assert r.kind == "non_arabic"
    assert any(b.get("lang") == "en" for b in r.blocks)


@pytest.mark.parametrize("message, key", [("السلام عليكم", "greeting_salam"), ("مرحبا", "greeting_general"),
                                          ("جزاك الله خير", "thanks")])
def test_smalltalk(message, key):
    r = ask(message)[1]
    assert (r.kind, r.blocks[0]["key"]) == ("smalltalk", key)


# --- lexical retrieval ---

@pytest.mark.parametrize("message, expected", [
    ("وش الدليل على وجود الله", "kawn-god-existence"),
    ("هل الكون صدفة", "kawn-universe-chance"),
    ("ليش فيه شر ومعاناة", "sharr-why-evil"),
    ("المسلمين يعبدون الكعبة", "tasawur-kaaba"),
    ("الكون جا بالصدفة؟", "kawn-universe-chance"),
])
def test_lexical_top_hit(loaded, message, expected):
    assert pipeline.STATE.index.search(message)[0][0] == expected


@pytest.mark.parametrize("message", ["كم ركعة صلاة الوتر؟", "وش أفضل مطعم في الرياض", "اشرح لي نظرية النسبية",
                                     "متى تأسست الدولة السعودية", "كيف أطبخ الكبسة"])
def test_out_of_scope_abstains_in_degraded_mode(loaded, message):
    r = ask(message, mode="offline")[1]
    assert r.kind in ("abstain", "refer")
    assert r.entry_id is None


def test_ruling_word_gets_fiqh_referral_in_degraded_mode(loaded):
    r = ask("ما حكم الموسيقى", mode="offline")[1]
    assert r.kind == "refer"
    assert r.blocks[0]["key"] == "referral_fiqh"


def test_no_entries_means_fixed_abstention():
    r = ask("هل الكون جاء صدفة؟")[1]
    if not pipeline.STATE.entries:
        assert r.kind == "abstain" and not r.degraded


# --- composition (step 11) ---

def test_answer_blocks_layers_and_verse_text_from_mushaf(loaded):
    r = ask("هل الكون جاء صدفة؟", mode="offline")[1]
    assert (r.kind, r.entry_id, r.degraded) == ("answer", "kawn-universe-chance", True)
    types = [b["type"] for b in r.blocks]
    assert types[0] == "notice"  # level C
    assert types[1:] == ["answer", "sharia", "sources", "review", "related"]
    verse = r.blocks[2]["verses"][0]
    assert verse["label"] == "الطور: 35"
    assert verse["text"].startswith("أَمۡ خُلِقُواْ")


def test_final_check_rejects_altered_verse(loaded):
    e = pipeline.STATE.entries["kawn-god-existence"]
    blocks = compose.answer_blocks(e, pipeline.STATE.entries)
    blocks[1]["verses"][0]["text"] = "نص محرف"
    with pytest.raises(AssertionError):
        compose.final_check(blocks, e)


def test_api_answer_roundtrip(loaded):
    body = client.post("/api/chat", json={"message": "ليش فيه شر في الدنيا؟", "mode": "offline"}).json()
    assert body["kind"] == "answer" and body["entry_id"] == "sharr-why-evil"


def test_non_arabic_term_gets_approved_glossary_meaning(monkeypatch):
    monkeypatch.setattr(pipeline, "load_glossary", lambda: [{
        "key": "jihad", "match": ["jihad"], "term_en": "Jihad", "term_ar": "الجهاد",
        "definition_en": "A test definition.", "url": "https://terminologyenc.com/en/browse/term/1"}])
    r = ask("What does jihad mean in Islam?")[1]
    assert r.kind == "non_arabic"
    assert r.blocks[0]["type"] == "glossary" and r.blocks[0]["items"][0]["term_ar"] == "الجهاد"
