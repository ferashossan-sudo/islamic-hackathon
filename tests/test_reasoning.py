"""«بالعقل والعلم»: the reviewed reasoning layer — validation, the answer block, the dialogue material, G13, kb_tool."""
import asyncio
import copy
import dataclasses
import importlib.util
import json
from pathlib import Path

import pytest

from app import compose, converse, guards, kb, main, pipeline, router
from app.schemas import ChatRequest
from tests.test_lexical_line import entry

ROOT = Path(__file__).resolve().parent.parent
SCIENCE = [{"claim": "للكون بداية زمنية", "degree": "leading_theory", "source": "U.S. Department of Energy, DOE Explains: Cosmology",
            "url": "https://www.energy.gov/science/doe-explainscosmology", "licence": "Public domain (U.S. Department of Energy)"}]
REASONING = {
    "steps": [
        {"text": "كل ما يبدأ في الوجود لا يُحدث نفسه بنفسه، فلا بد له من سبب أوجده.", "basis": "aql",
         "source": "الدرر السنية", "url": "https://dorar.net/aqeeda/1"},
        {"text": "ويرجّح علم الكونيات أن للكون بداية زمنية قبل نحو 13.8 مليار سنة، كما تدل أرصاد هابل لتباعد المجرات.",
         "basis": "ilm", "source": "DOE Explains: Cosmology", "url": "https://www.energy.gov/science/doe-explainscosmology",
         "locator": "الفقرة 2"},
        {"text": "فالكون إذن يحتاج إلى سبب من خارجه، لا يتقيد بالزمان ولا بالمادة.", "basis": "aql",
         "source": "الدرر السنية", "url": "https://dorar.net/aqeeda/1"},
    ],
    "objections": [
        {"objection": "طيب ومن خلق الخالق؟",
         "response": "القاعدة أن ما له بداية يحتاج سبباً، لا أن كل موجود يحتاج سبباً؛ والخالق لا بداية له، "
                     "فالسؤال عن خالقه سؤال عن بداية ما لا بداية له.",
         "source": "الدرر السنية", "url": "https://dorar.net/aqeeda/1", "locator": "المبحث الأول"},
        {"objection": "ليش ما يكون الكون أزلياً لا بداية له؟",
         "response": "لأن الأرصاد ترجّح أن له بداية، ولأن سلسلة من الحوادث لا أول لها لا تصل إلى يومنا هذا أبداً.",
         "source": "الدرر السنية", "url": "https://dorar.net/aqeeda/1"},
    ],
}
VARIANTS = ["صيغة أولى", "صيغة ثانية", "صيغة ثالثة", "صيغة رابعة"]


def with_reasoning(entry_id="kawn-reason-x", level="B", reasoning=None, **extra):
    """An approved entry whose «بالعقل والعلم» layer is approved too (its own hash in the review)."""
    e = entry(entry_id, "ما الدليل على أن للكون خالقاً؟", VARIANTS, level=level, science=copy.deepcopy(SCIENCE),
              reasoning=copy.deepcopy(reasoning or REASONING), **extra)
    e["review"]["reasoning_hash"] = kb.reasoning_hash(e)
    return e


def draft(**kw):
    """The same entry before approval, so a test may change it and still expect no blocking error."""
    e = with_reasoning(**kw)
    e["status"] = "draft"
    e.pop("review")
    return e


# --- validation (app/kb.py) ---

def test_good_reasoning_has_no_errors_or_warnings():
    errors, warnings = kb.validate_entry(with_reasoning())
    assert errors == []
    assert not [w for w in warnings if w.startswith("العقل والعلم")]


def test_reasoning_is_optional():
    e = draft()
    del e["reasoning"]
    assert kb.validate_entry(e)[0] == []


def test_reasoning_has_its_own_approval():
    """Changing the layer leaves the answer's approval intact; the changed layer is simply not served."""
    e = with_reasoning()
    before = kb.approved_hash(e)
    e["reasoning"]["steps"][0]["text"] += " تعديل"
    assert kb.approved_hash(e) == before
    assert not any("بعد اعتماده" in err for err in kb.validate_entry(e)[0])
    served = kb.approved_only([e])
    assert [x["id"] for x in served] == [e["id"]] and "reasoning" not in served[0]


def test_unreviewed_reasoning_is_never_served_and_never_blocks(tmp_path):
    e = with_reasoning()
    del e["review"]["reasoning_hash"]
    e["reasoning"]["steps"][0]["text"] = "{{q:52:35}}"  # would be a blocking error if it were served
    path = tmp_path / "kb.json"
    kb.write_all([e], path)
    loaded = kb.load_approved(path)
    assert [x["id"] for x in loaded] == [e["id"]] and "reasoning" not in loaded[0]


def test_reviewed_reasoning_is_served():
    e = with_reasoning()
    assert kb.approved_only([e])[0]["reasoning"] == e["reasoning"]
    assert kb.served_copy(e) is e


def test_kb_tool_approve_reasoning(tmp_path):
    tool = _kb_tool()
    e = with_reasoning()
    del e["review"]["reasoning_hash"]
    path = tmp_path / "kb.json"
    kb.write_all([e], path)
    assert tool.approve_reasoning([e["id"]], "2026-10-05", path) == 0
    stored = kb.read_all(path)[0]
    assert stored["review"]["reasoning_hash"] == kb.reasoning_hash(stored)
    assert stored["review"]["approved_hash"] == e["review"]["approved_hash"]
    assert "reasoning" in kb.approved_only([stored])[0]


def _step(i):
    return lambda r: r["steps"][i]


@pytest.mark.parametrize("mutate, expected", [
    (lambda r: r.update(steps=r["steps"][:1]), "الخطوات"),
    (lambda r: r.update(steps=r["steps"] * 2), "الخطوات"),
    (lambda r: r.update(objections=r["objections"] * 4), "الاعتراضات"),
    (lambda r: r.update(extra=1), "مفاتيح غير معروفة"),
    (lambda r: r["steps"][0].pop("source"), "حقول ناقصة"),
    (lambda r: r["steps"][0].update(text="  "), "حقول ناقصة"),
    (lambda r: r["steps"][0].update(note="x"), "حقول غير معروفة"),
    (lambda r: r["objections"][0].pop("response"), "حقول ناقصة"),
    (lambda r: r["objections"][0].update(grade="x"), "حقول غير معروفة"),
    (lambda r: r["steps"][0].update(basis="naql"), "الأساس"),
    (lambda r: r["steps"][0].update(url="https://example.com/x"), "خارج القائمة"),
    (lambda r: r["steps"][0].update(url="http://dorar.net/aqeeda/1"), "خارج القائمة"),
    (lambda r: r["objections"][1].update(url="https://blog.example.org/a"), "خارج القائمة"),
    (lambda r: r["steps"][0].update(text="قال تعالى {{q:52:35}} فلا بد من خالق."), "مرجع آية أو حديث"),
    (lambda r: r["objections"][0].update(response="كما في {{h:1}} الإنسان يولد على الفطرة."), "مرجع آية أو حديث"),
    (lambda r: r["steps"][2].update(text="قال تعالى ﴿أم خلقوا من غير شيء﴾ فلا بد من خالق."), "﴿﴾"),
    (lambda r: r["objections"][0].update(response="وهذا رواه البخاري في صحيحه."), "«رواه»"),
    (lambda r: r["steps"][0].update(text="وقد رَوَاهُ مسلم عن النبي ﷺ."), "«رواه»"),
    (lambda r: r["steps"][1].update(url="https://www.energy.gov/science/articles/origins-universe"), "معلومة علمية"),
])
def test_blocking_reasoning_rules(mutate, expected):
    e = with_reasoning()
    mutate(e["reasoning"])
    errors, _ = kb.validate_entry(e)
    assert any(expected in err and err.startswith("العقل والعلم") for err in errors), errors


def test_reasoning_must_be_an_object():
    e = with_reasoning()
    e["reasoning"] = ["خطوة"]
    assert any("كائناً" in err for err in kb.validate_entry(e)[0])


def test_narrators_word_is_not_mistaken_for_rawahu():
    e = draft()
    e["reasoning"]["steps"][0]["text"] = "ينقل الرواة الأخبار، والعقل يزن ما يُنقل إليه."
    assert kb.validate_entry(e)[0] == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda r: r["steps"][0].update(text="س" * 281), "الحد المقترح 280"),
    (lambda r: r["objections"][0].update(objection="س" * 201), "الحد المقترح 200"),
    (lambda r: r["objections"][0].update(response="س" * 451), "الحد المقترح 450"),
    (lambda r: r["steps"][0].update(text="وهذا من الإعجاز في الخلق."), "«الإعجاز»"),
    (lambda r: r["objections"][0].update(response="وقد أثبت العلم أن للكون بداية."), "«أثبت العلم»"),
    (lambda r: r["steps"][0].update(text="أم خلقوا من غير شيء أم هم الخالقون، فلا بد من خالق."), "أربع كلمات"),
])
def test_reasoning_warnings_do_not_block(mutate, expected):
    e = draft()
    mutate(e["reasoning"])
    errors, warnings = kb.validate_entry(e)
    assert errors == []
    assert any(expected in w for w in warnings), warnings


# --- the answer block and step 11 (app/compose.py) ---

def test_reasoning_block_comes_after_the_answer_and_before_the_sharia_texts():
    e = with_reasoning()
    blocks = compose.answer_blocks(e, {})
    types = [b["type"] for b in blocks]
    assert types.index("answer") + 1 == types.index("reasoning") < types.index("sharia")
    block = blocks[types.index("reasoning")]
    assert block["title"] == "بالعقل والعلم" and block["objections_title"] == "اعتراضات شائعة وجوابها"
    assert [s["text"] for s in block["steps"]] == [s["text"] for s in REASONING["steps"]]
    assert [s["basis_label"] for s in block["steps"]] == ["استدلال عقلي", "معلومة علمية", "استدلال عقلي"]
    assert [(s["source"], s["locator"], s["url"]) for s in block["steps"]] == [
        (s["source"], s.get("locator", ""), s["url"]) for s in REASONING["steps"]]
    assert [(o["objection"], o["response"], o["locator"], o["url"]) for o in block["objections"]] == [
        (o["objection"], o["response"], o.get("locator", ""), o["url"]) for o in REASONING["objections"]]


def test_no_reasoning_block_without_reasoning():
    e = with_reasoning()
    del e["reasoning"]
    assert "reasoning" not in [b["type"] for b in compose.answer_blocks(e, {})]


def test_no_objections_still_shows_the_steps():
    e = with_reasoning(reasoning={**REASONING, "objections": []})
    block = next(b for b in compose.answer_blocks(e, {}) if b["type"] == "reasoning")
    assert len(block["steps"]) == 3 and block["objections"] == []


@pytest.mark.parametrize("tamper", [
    lambda b: b["steps"][0].update(text="خطوة لم يراجعها أحد."),
    lambda b: b["steps"][1].update(basis_label="استدلال عقلي"),
    lambda b: b["steps"][0].update(url="https://dorar.net/other"),
    lambda b: b["steps"].pop(),
    lambda b: b["steps"].append(dict(b["steps"][0])),
    lambda b: b["steps"][0].update(extra="نص زائد"),
    lambda b: b["objections"][0].update(response="جواب آخر."),
    lambda b: b["objections"].pop(),
    lambda b: b.update(note="زيادة"),
])
def test_final_check_catches_a_tampered_reasoning_block(tamper):
    e = with_reasoning()
    blocks = compose.answer_blocks(e, {})
    block = next(b for b in blocks if b["type"] == "reasoning")
    tamper(block)
    with pytest.raises(AssertionError):
        compose.final_check(blocks, e)


def test_final_check_refuses_a_reasoning_block_for_an_entry_without_reasoning():
    e = with_reasoning()
    block = compose.reasoning_block(e["reasoning"])
    del e["reasoning"]
    with pytest.raises(AssertionError):
        compose.final_check([block], e)


# --- the dialogue material (app/converse.py) and G14 ---

def test_material_carries_the_reasoning_in_order():
    m = converse.material(with_reasoning())["reasoning"]
    assert m["steps"] == [{"text": REASONING["steps"][0]["text"], "basis": "aql"},
                          {"text": REASONING["steps"][1]["text"], "basis": "ilm", "degree": "نظرية راجحة"},
                          {"text": REASONING["steps"][2]["text"], "basis": "aql"}]
    assert m["objections"] == [{"objection": o["objection"], "response": o["response"]} for o in REASONING["objections"]]
    assert "source" not in json.dumps(m) and "url" not in json.dumps(m)


def test_material_has_empty_reasoning_lists_when_absent():
    e = with_reasoning()
    del e["reasoning"]
    assert converse.material(e)["reasoning"] == {"steps": [], "objections": []}


SETTINGS = dataclasses.replace(main.settings, llm_enabled=True, router_provider="gemini", gemini_api_key="k",
                               converse_enabled=True)
# A reply built from the reasoning layer only: its number, its name and its objection's answer.
FROM_REASONING = ("سؤالك عن أصل الكون في محله.\nالعقل يقول إن ما يبدأ لا يُحدث نفسه. ويرجّح علم الكونيات، بحسب أرصاد "
                  "هابل، أن للكون بداية قبل نحو 13.8 مليار سنة، وهذه نظرية راجحة.\nوإن سألت من خلق الخالق، فالقاعدة "
                  "أن ما له بداية يحتاج سبباً، والخالق لا بداية له.\nوش رأيك، هل يمكن لشيء يبدأ أن يوجد نفسه؟")


def test_verifier_payload_includes_the_reasoning(monkeypatch):
    seen = []

    async def fake_verify(s, payload, timeout):
        seen.append(json.loads(payload))
        return json.dumps({"unsupported": []}), {}

    monkeypatch.setitem(converse.VERIFIERS, "gemini", fake_verify)
    assert asyncio.run(converse.unsupported_claims(FROM_REASONING, with_reasoning(), SETTINGS)) == []
    assert seen[0]["material"]["reasoning"]["objections"][0]["objection"] == "طيب ومن خلق الخالق؟"


def test_prompts_describe_the_reasoning_layer():
    assert "material.reasoning.objections" in converse.RULES and "material.reasoning.steps" in converse.RULES
    assert "reasoning" in converse.VERIFY_RULES


# --- G13 (app/guards.py) ---

def test_g13_accepts_a_reply_built_from_the_reasoning():
    e = with_reasoning()
    assert guards.reply_problem(FROM_REASONING, e, ["الدرر السنية"]) is None
    bare = {k: v for k, v in e.items() if k != "reasoning"}
    assert guards.reply_problem(FROM_REASONING, bare, ["الدرر السنية"]) is not None  # 13.8 and هابل come from it


def test_g13_level_c_holder_named_in_the_reasoning_counts():
    step = {"text": "ويرى ابن تيمية أن جمهور العقلاء يقرون بأن ما يحدث له محدث.", "basis": "aql",
            "source": "الدرر السنية", "url": "https://dorar.net/aqeeda/1"}
    e = with_reasoning("kawn-reason-c", level="C", reasoning={**REASONING, "steps": [step, *REASONING["steps"][1:]]})
    reply = FROM_REASONING.replace("وش رأيك،", "ويرى ابن تيمية أن جمهور العقلاء يقرون بذلك. وش رأيك،")
    assert guards.reply_problem(reply, e, ["الدرر السنية"]) is None
    bare = {**e, "reasoning": REASONING}
    assert guards.reply_problem(reply, bare, ["الدرر السنية"]) == "attribution"


@pytest.fixture
def chat_fakes(monkeypatch):
    before = (pipeline.STATE.entries, pipeline.STATE.index, pipeline.STATE.source_names)
    pipeline.load([with_reasoning()])
    state = {"payloads": []}

    async def fake_router(s, system, payload, timeout):
        return json.dumps({"route": "knowledge", "entry_id": "kawn-reason-x", "confidence": "high",
                           "oos_reason": "none", "evidence_request": "none", "framing": ""}), {}

    async def fake_compose(s, payload, timeout):
        state["payloads"].append(json.loads(payload))
        return json.dumps({"reply": FROM_REASONING}, ensure_ascii=False), {}

    async def fake_verify(s, payload, timeout):
        state["payloads"].append(json.loads(payload))
        return json.dumps({"unsupported": []}), {}

    monkeypatch.setitem(router.PROVIDERS, "gemini", fake_router)
    monkeypatch.setitem(converse.PROVIDERS, "gemini", fake_compose)
    monkeypatch.setitem(converse.VERIFIERS, "gemini", fake_verify)
    yield state
    pipeline.STATE.entries, pipeline.STATE.index, pipeline.STATE.source_names = before


def test_an_objection_answered_from_the_reasoning_is_shown_above_the_card(chat_fakes):
    r = asyncio.run(pipeline.handle(ChatRequest(message="طيب ومين خلق الله؟"), SETTINGS))[1]
    types = [b["type"] for b in r.blocks]
    assert types[0] == "chat" and "reasoning" in types
    assert all(p["material"]["reasoning"]["steps"] for p in chat_fakes["payloads"])  # converse and G14 both see it


def test_frontend_renders_the_reasoning_block_with_text_content_only():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert "reasoning(card, b)" in js and '"block-reasoning"' in js and '"objection"' in js
    assert ".objection" in css and ".reasoning-steps" in css


# --- scripts/kb_tool.py ---

def _kb_tool():
    spec = importlib.util.spec_from_file_location("kb_tool_under_test", ROOT / "scripts" / "kb_tool.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _draft(entry_id, **extra):
    e = entry(entry_id, "ما الدليل على أن للكون خالقاً؟", VARIANTS, science=copy.deepcopy(SCIENCE), **extra)
    e["status"] = "draft"
    e.pop("review")
    return e


@pytest.fixture
def store(tmp_path):
    """A temporary content/kb.json and private/drafts: the real files are never touched."""
    kb_path = tmp_path / "kb.json"
    kb.write_all([_draft("kawn-batch-one"), entry("kawn-approved-one", "سؤال معتمد؟", VARIANTS)], kb_path)
    drafts = tmp_path / "drafts"
    (drafts / "batch2").mkdir(parents=True)
    (drafts / "batch3").mkdir()
    crlf = json.dumps([_draft("kawn-later")], ensure_ascii=False, indent=2).replace("\n", "\r\n")
    (drafts / "batch2" / "kawn-later.json").write_bytes(crlf.encode("utf-8"))
    broken = _draft("kawn-later")
    broken["source"] = {"name": "مدونة", "url": "https://example.com/x"}  # a later copy that fails validation
    (drafts / "batch3" / "kawn-later.json").write_text(json.dumps([broken], ensure_ascii=False, indent=2) + "\n",
                                                        encoding="utf-8")
    return kb_path, drafts, tmp_path


def _stage(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_merge_reasoning_into_batch_one_and_into_the_last_valid_draft(store, capsys):
    kb_path, drafts, tmp = store
    tool = _kb_tool()
    staged = [_stage(tmp, "a.json", {"id": "kawn-batch-one", "reasoning": REASONING}),
              _stage(tmp, "b.json", [{"id": "kawn-later", "reasoning": REASONING}])]
    broken_before = (drafts / "batch3" / "kawn-later.json").read_bytes()
    assert tool.merge_reasoning(staged, kb_path, drafts) == 0
    merged = next(e for e in kb.read_all(kb_path) if e["id"] == "kawn-batch-one")
    assert merged["reasoning"] == REASONING and merged["status"] == "draft"
    raw = (drafts / "batch2" / "kawn-later.json").read_bytes()
    assert b"\r\n" in raw and not raw.endswith(b"\n")  # written back in its own style
    assert json.loads(raw.decode("utf-8"))[0]["reasoning"] == REASONING
    assert (drafts / "batch3" / "kawn-later.json").read_bytes() == broken_before  # the invalid copy is not served
    assert "✓ kawn-batch-one" in capsys.readouterr().out


@pytest.mark.parametrize("data, expected", [
    ({"id": "kawn-batch-one", "reasoning": {**REASONING, "steps": REASONING["steps"][:1]}}, "الخطوات"),
    ({"id": "kawn-batch-one", "reasoning": {"steps": [{**REASONING["steps"][0], "text": "قال {{q:52:35}}"},
                                                      REASONING["steps"][1]]}}, "مرجع آية"),
    ({"id": "kawn-approved-one", "reasoning": REASONING}, "ليس من المعلومات العلمية"),
    ({"id": "kawn-missing", "reasoning": REASONING}, "غير موجود"),
    ({"id": "kawn-batch-one", "reasoning": REASONING, "status": "approved"}, "الصيغة المطلوبة"),
])
def test_merge_reasoning_refuses_an_entry_that_would_fail_validation(store, capsys, data, expected):
    kb_path, drafts, tmp = store
    before = kb_path.read_bytes()
    assert _kb_tool().merge_reasoning([_stage(tmp, "bad.json", data)], kb_path, drafts) == 1
    assert kb_path.read_bytes() == before
    assert expected in capsys.readouterr().out


def test_review_digests_show_the_reasoning_layer(store):
    kb_path, drafts, tmp = store
    tool = _kb_tool()
    tool.merge_reasoning([_stage(tmp, "a.json", {"id": "kawn-later", "reasoning": REASONING})], kb_path, drafts)
    later = json.loads((drafts / "batch2" / "kawn-later.json").read_text(encoding="utf-8"))[0]
    full = tool._entry_html(later)
    assert "بالعقل والعلم (جديد)" in full and REASONING["objections"][0]["response"] in full
    out = tmp / "reasoning.html"
    assert tool.reasoning_digest(None, out, "ملحق", kb_path, drafts) == 0
    html = out.read_text(encoding="utf-8")
    assert "kawn-later" in html and "kawn-batch-one" not in html  # only entries that have the layer
    assert REASONING["steps"][1]["text"] in html and "استدلال عقلي" in html and later["question"] in html
    assert "نص فيه" not in html  # the reasoning layer only, not the full answer
    assert tool.reasoning_digest(["kawn-batch-one", "kawn-later"], out, "ملحق", kb_path, drafts) == 0
    assert "kawn-batch-one" in out.read_text(encoding="utf-8")


def test_merge_reasoning_into_an_approved_entry_keeps_its_approval(store):
    """The layer has its own approval: merging it leaves the answer live, and the new layer waits for review."""
    kb_path, drafts, tmp = store
    aql_only = {"steps": [s for s in REASONING["steps"] if s["basis"] == "aql"], "objections": REASONING["objections"]}
    assert _kb_tool().merge_reasoning([_stage(tmp, "ok.json", {"id": "kawn-approved-one", "reasoning": aql_only})],
                                      kb_path, drafts) == 0
    stored = next(e for e in kb.read_all(kb_path) if e["id"] == "kawn-approved-one")
    assert stored["reasoning"] == aql_only
    served = kb.approved_only([stored])
    assert [e["id"] for e in served] == ["kawn-approved-one"] and "reasoning" not in served[0]


def test_science_overclaim_phrases_are_guarded():
    """«العلم يقول عكس كذا» about a leading theory: G13 rejects it unless the material itself says it."""
    e = with_reasoning()
    reply = "بس العلم يقول عكس كذا، فالكون له بداية. وش رايك؟"
    assert guards.reply_problem(reply, e, []) == "phrase:العلم يقول"


@pytest.mark.parametrize("reply, problem", [
    ("تذكر وزارة الطاقة في نظرية راجحة أن الكون تمدد من حال ابتدائية، وهذا ينفي كونه أزلياً. وش رايك؟", "overclaim"),
    ("ويرجّح العلم أن للكون بداية فهو حادث قطعاً. تشوف كذا؟", "overclaim"),
    ("هي إلى اليوم فرضية لا يثبتها دليل معتبر، فلا تغني عن الخالق. وش تشوف؟", None),
    ("تذكر وزارة الطاقة في نظرية راجحة أن الكون تمدد من حال ابتدائية. وما حدث بعد عدمه لا بد له من محدث، "
     "وهذا يثبت الحاجة إلى خالق. وش رايك؟", None),
])
def test_leading_theory_never_settles_a_point(reply, problem):
    e = with_reasoning()
    got = guards.reply_problem(reply, e, [])
    assert (got == "overclaim") == (problem == "overclaim"), got
