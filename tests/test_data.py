import copy
import json
import re
import unicodedata
from pathlib import Path

import pytest

from app import arabic, kb, quran

ROOT = Path(__file__).resolve().parent.parent
KFGQPC = ROOT / "docs" / "prep" / "quran" / "kfgqpc" / "kfgqpc_hafs_v30-data" / "kfgqpc_hafs_v30.json"


# --- arabic.py ---

def test_normalize_strips_marks_and_unifies_letters():
    assert arabic.normalize("أَمْ خُلِقُوا مِنْ غَيْرِ شَيْءٍ") == "ام خلقوا من غير شي"
    assert arabic.normalize("إِنَّ الصَّلاةَ") == arabic.normalize("ان الصلاه")
    assert arabic.normalize("هــــل") == "هل"
    assert arabic.normalize("مُوسَىٰ") == "موسي"
    assert arabic.normalize("٢١ ۳") == "21 3"
    assert arabic.normalize("  كلمة​   أخرى ") == "كلمه اخري"


def test_words_drop_punctuation():
    assert arabic.words("هل الكون صدفة؟!") == ["هل", "الكون", "صدفه"]


def test_arabic_ratio():
    assert arabic.arabic_ratio("ما معنى الجهاد؟") == 1.0
    assert arabic.arabic_ratio("What does jihad mean in Islam?") == 0.0
    assert arabic.arabic_ratio("123 ؟") == 1.0


# --- quran.py (G2) ---

def test_mushaf_has_all_verses():
    assert len(quran.verses()) == quran.TOTAL_VERSES


@pytest.mark.parametrize("ref", ["52:35", "51:47", "21:30"])
def test_lookup_returns_uthmani_text(ref):
    [verse] = quran.lookup(ref)
    assert verse.ref == ref
    assert verse.text and verse.plain
    assert "﻿" not in verse.text


def test_review_verses_match_expected_letters():
    assert arabic.normalize(quran.lookup("52:35")[0].plain) == "ام خلقوا من غير شي ام هم الخالقون"
    assert "بِأَيْدٍ" in quran.lookup("51:47")[0].plain


def test_surah_names_and_ranges():
    assert quran.parse_ref("الطور:35") == [(52, 35)]
    assert quran.parse_ref("سورة الطور:35") == [(52, 35)]
    assert quran.parse_ref("الأنبياء:30-31") == [(21, 30), (21, 31)]
    assert quran.label("21:30-31") == "الأنبياء: 30-31"
    assert quran.label("52:35") == "الطور: 35"


@pytest.mark.parametrize("bad", ["52:50", "115:1", "الطور", "0:1", "abc:1", "52:36-35"])
def test_bad_refs_raise(bad):
    with pytest.raises(ValueError):
        quran.parse_ref(bad)


def test_alignment_verses_use_kfgqpc_text():
    alignment = json.loads((quran.DATA / "kfgqpc_alignment.json").read_text(encoding="utf-8"))["verses"]
    assert sorted(alignment) == ["11:41", "27:20", "36:22", "52:37"]
    for ref, text in alignment.items():
        assert quran.lookup(ref)[0].text == text


@pytest.mark.skipif(not KFGQPC.exists(), reason="KFGQPC v30 file is local only (not redistributable)")
def test_display_text_equals_kfgqpc_v30_for_every_verse():
    rows = json.loads(KFGQPC.read_text(encoding="utf-8"))
    end = re.compile(r"\s*[۝﴾﴿]?\s*[٠-٩۰-۹]+\s*$")
    mismatched = []
    for row in rows:
        expected = unicodedata.normalize("NFC", " ".join(end.sub("", row["aya_text_unicode"]).split()))
        if quran.verses()[(row["sura_no"], row["aya_no"])].text != expected:
            mismatched.append(f"{row['sura_no']}:{row['aya_no']}")
    assert mismatched == []


# --- kb.py (schema, approval hash, G1) ---

GOOD = {
    "id": "kawn-god-existence", "theme": "kawn", "level": "B", "status": "draft",
    "question": "كيف أعرف أن الله موجود؟", "variants": ["وش الدليل على وجود الله؟", "هل الله موجود؟", "دليل وجود الخالق"],
    "summary": "خلاصة تجريبية.", "body": "نص تجريبي فيه آية {{q:الطور:35}}.", "explain_simple": "",
    "transfer": "paraphrase", "source": {"name": "الدرر السنية", "url": "https://dorar.net/aqeeda/1", "locator": ""},
    "verses": ["الطور:35"],
    "tafsir": [{"mufassir": "الطبري", "summary": "خلاصة تفسير.", "source": "الموسوعة التفسيرية", "url": "https://dorar.net/tafseer/52/2"}],
    "hadiths": [], "science": [{"claim": "للكون بداية زمنية", "degree": "leading_theory", "source": "U.S. Department of Energy, DOE Explains: Cosmology",
                                "url": "https://www.energy.gov/science/doe-explainscosmology", "licence": "Public domain (U.S. Department of Energy)"}],
    "related": [], "featured": False, "prepared_by": "فراس", "drafted_with_ai": True,
}


def good():
    return kb.normalize_refs(copy.deepcopy(GOOD))


def approve(entry):
    entry["status"] = "approved"
    entry["review"] = {"reviewer": "المراجع الشرعي للفريق", "reviewed_at": "2026-10-04", "note": "",
                       "approved_hash": kb.approved_hash(entry)}
    return entry


def test_normalize_refs_rewrites_names_to_numbers():
    entry = good()
    assert entry["verses"] == ["52:35"]
    assert "{{q:52:35}}" in entry["body"]


def test_good_entry_has_no_errors():
    errors, warnings = kb.validate_entry(good())
    assert errors == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda e: e.update(id="Bad Id"), "المعرّف"),
    (lambda e: e.update(level="D"), "المستوى"),
    (lambda e: e.update(body="قال تعالى ﴿أم خلقوا من غير شيء﴾"), "نص آية"),
    (lambda e: e.update(body="{{q:52:99}}"), "غير موجودة"),
    (lambda e: e["science"][0].update(degree="certain"), "الدرجة"),
    (lambda e: e["science"][0].pop("licence"), "حقول ناقصة"),
    (lambda e: e["source"].update(url="https://science.nasa.gov/x"), "خارج القائمة"),
    (lambda e: e["tafsir"][0].update(url="https://islamqa.info/ar/1"), "مصادر التفسير"),
    (lambda e: e.update(hadiths=[{"text": "نص", "source": "الترمذي"}]), "الستة"),
    (lambda e: e.update(hadiths=[{"text": "نص", "source": "س", "number": "1", "grade": "ضعيف", "grader": "الألباني",
                                  "url": "https://dorar.net/h/1", "purpose": "evidence"}]), "show_weakness"),
])
def test_blocking_rules(mutate, expected):
    entry = good()
    mutate(entry)
    errors, _ = kb.validate_entry(entry)
    assert any(expected in e for e in errors), errors


def test_hash_ignores_variants_but_not_visible_fields():
    entry = approve(good())
    before = entry["review"]["approved_hash"]
    entry["variants"].append("صيغة عامية جديدة")
    assert kb.approved_hash(entry) == before
    entry["summary"] = "خلاصة معدلة."
    assert kb.approved_hash(entry) != before
    errors, _ = kb.validate_entry(entry)
    assert any("بعد اعتماده" in e for e in errors)


def test_approved_only_excludes_drafts_and_stale_approvals():
    draft = good()
    approved = approve(good())
    approved["id"] = "kawn-other"
    stale = approve(good())
    stale["id"] = "kawn-stale"
    stale["body"] = "تغيير بعد الاعتماد"
    assert [e["id"] for e in kb.approved_only([draft, approved, stale])] == ["kawn-other"]


def test_load_approved_refuses_broken_approved_entry(tmp_path):
    entry = approve(good())
    entry["summary"] = "تغيّر بعد الاعتماد"
    path = tmp_path / "kb.json"
    path.write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(kb.KBError):
        kb.load_approved(path)


def test_repo_kb_is_valid():
    kb.load_approved()  # the committed kb.json must always load


def test_runtime_files_are_tracked_by_git():
    """Everything the server reads at start must be in the repo (a clean clone or a deploy must run)."""
    import subprocess
    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8").stdout.splitlines())
    needed = ["data/quran/mushafs-1.json.gz", "data/quran/mushafs-2.json.gz", "data/quran/kfgqpc_alignment.json",
              "content/fixed_texts.json", "content/kb.json", "content/approved_sources.json",
              "content/distress_terms.json", "content/framing_lexicon.json", "app/prompts/router_v1.md",
              "app/templates/index.html", "static/app.js", "static/styles.css", "requirements.txt", "render.yaml"]
    assert [p for p in needed if p not in tracked] == []


def test_more_sources_are_checked_and_shown_under_the_answer():
    from app import compose, kb
    from tests.test_lexical_line import entry
    more = [{"name": "موقع الشيخ ابن باز: فتوى 5966", "url": "https://binbaz.org.sa/fatwas/5966"}]
    e = entry("kawn-sources-x", "سؤال؟", ["صيغة أولى", "صيغة ثانية", "صيغة ثالثة", "صيغة رابعة"], more_sources=more)
    assert not kb.validate_entry(e)[0]
    sources = next(b for b in compose.answer_blocks(e, {}) if b["type"] == "sources")
    assert [i["url"] for i in sources["items"]][1:] == ["https://binbaz.org.sa/fatwas/5966"]
    bad = {**e, "more_sources": [{"name": "مدونة", "url": "https://example.com/x"}]}
    assert any("المصدر الإضافي" in err for err in kb.validate_entry(bad)[0])


def test_a_hadith_outside_the_two_sahihs_needs_a_check_link_from_the_package_sources():
    from app import kb
    from tests.test_lexical_line import entry
    h = {"text": "نص", "source": "سنن أبي داود", "number": "1", "grade": "صحيح", "grader": "الألباني",
         "url": "https://hadeethenc.com/ar/browse/hadith/1", "via": "hadeethenc", "purpose": "evidence"}
    e = {**entry("kawn-hadith-x", "سؤال؟", ["صيغة أولى", "صيغة ثانية", "صيغة ثالثة", "صيغة رابعة"]),
         "status": "draft", "hadiths": [h]}
    assert any("الصحيحين" in err for err in kb.validate_entry(e)[0])
    ok = {**e, "hadiths": [{**h, "verify_url": "https://dorar.net/h/abc"}]}
    assert not kb.validate_entry(ok)[0]
    bad = {**e, "hadiths": [{**h, "verify_url": "https://example.com/h"}]}
    assert any("رابط التحقق" in err for err in kb.validate_entry(bad)[0])


def test_question_text_fills_verse_placeholders_from_the_mushaf():
    from app import compose
    shown = compose.question_text("هل تدل آية الذاريات {{q:51:47}} على أن الكون يتمدد؟")
    assert "{{" not in shown
    assert "﴿" + quran.lookup("51:47")[0].plain + "﴾" in shown
    assert quran.find_misquote(shown) is None  # tapping the chip must not trigger the misquote notice
    assert compose.question_text("سؤال بلا آية") == "سؤال بلا آية"
