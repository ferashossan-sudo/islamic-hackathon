"""The knowledge base: schema rules, the approval hash, and loading approved entries only (G1).

Every rule here is blocking: an approved entry that breaks one stops the server from starting.
"""
import hashlib
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from app import quran

ROOT = Path(__file__).resolve().parent.parent
KB_PATH = ROOT / "content" / "kb.json"
SOURCES_PATH = ROOT / "content" / "approved_sources.json"

THEMES = ("kawn", "sharr", "wahy", "insan", "tasawur")
LEVELS = ("A", "B", "C")
STATUSES = ("draft", "needs_edit", "approved", "rejected")
DEGREES = ("fact", "leading_theory", "hypothesis")
WEAK_GRADES = ("ضعيف", "موضوع", "منكر", "لا أصل له", "باطل")
HADITH_FIELDS = ("text", "source", "number", "grade", "grader", "url")
SCIENCE_FIELDS = ("claim", "degree", "source", "url", "licence")
TAFSIR_FIELDS = ("mufassir", "summary", "source", "url")
# Fields the user sees. Changing any of them after approval invalidates the approval.
VISIBLE_FIELDS = ("question", "summary", "body", "explain_simple", "verses", "tafsir",
                  "hadiths", "science", "source", "level", "related")
TEXT_FIELDS = ("question", "summary", "body", "explain_simple")

ID_PATTERN = re.compile(r"^(kawn|sharr|wahy|insan|tasawur)-[a-z0-9-]+$")
VERSE_PLACEHOLDER = re.compile(r"\{\{q:([^}]+)\}\}")
ORNATE_BRACKETS = re.compile("[﴾﴿]")
WARN_PHRASES = ("الإعجاز", "سبق القرآن", "أثبت العلم")


class KBError(Exception):
    pass


@lru_cache(maxsize=1)
def approved_sources() -> dict:
    return json.loads(SOURCES_PATH.read_text(encoding="utf-8"))


def approved_hash(entry: dict) -> str:
    visible = {field: entry.get(field) for field in VISIBLE_FIELDS}
    canonical = json.dumps(visible, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _domain_ok(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return urlparse(url).scheme == "https" and any(host == d or host.endswith("." + d) for d in domains)


def normalize_refs(entry: dict) -> dict:
    """Rewrite {{q:الطور:35}} and 'الطور:35' as numeric '52:35'. Raises ValueError for a missing verse."""
    def numeric(ref: str) -> str:
        keys = quran.parse_ref(ref)
        sura, first = keys[0]
        last = keys[-1][1]
        return f"{sura}:{first}" if first == last else f"{sura}:{first}-{last}"

    for field in TEXT_FIELDS:
        if entry.get(field):
            entry[field] = VERSE_PLACEHOLDER.sub(lambda m: "{{q:" + numeric(m.group(1)) + "}}", entry[field])
    entry["verses"] = [numeric(ref) for ref in entry.get("verses", [])]
    return entry


def validate_entry(entry: dict) -> tuple[list[str], list[str]]:
    """Blocking errors and reviewer warnings for one entry."""
    errors, warnings = [], []
    sources = approved_sources()
    entry_id = entry.get("id", "")

    if not ID_PATTERN.match(entry_id):
        errors.append(f"المعرّف لا يطابق النمط: {entry_id!r}")
    elif entry.get("theme") != entry_id.split("-")[0]:
        errors.append(f"المحور {entry.get('theme')!r} لا يطابق بادئة المعرّف")
    if entry.get("level") not in LEVELS:
        errors.append(f"المستوى يجب أن يكون A أو B أو C، لا {entry.get('level')!r}")
    if entry.get("status") not in STATUSES:
        errors.append(f"الحالة غير معروفة: {entry.get('status')!r}")
    for field in ("question", "summary", "body"):
        if not str(entry.get(field) or "").strip():
            errors.append(f"الحقل {field} فارغ")
    source = entry.get("source") or {}
    if not source.get("name") or not source.get("url"):
        errors.append("المصدر يحتاج اسماً ورابطاً")
    elif not _domain_ok(source["url"], sources["domains"]):
        errors.append(f"رابط المصدر خارج القائمة المعتمدة: {source['url']}")

    for field in TEXT_FIELDS:
        text = entry.get(field) or ""
        if ORNATE_BRACKETS.search(text):
            errors.append(f"نص آية مكتوب في {field}: تُكتب الآيات مراجع {{{{q:سورة:آية}}}} فقط")
        for ref in VERSE_PLACEHOLDER.findall(text):
            try:
                quran.parse_ref(ref)
            except ValueError as exc:
                errors.append(f"{field}: {exc}")
    for ref in entry.get("verses", []):
        try:
            quran.parse_ref(ref)
        except ValueError as exc:
            errors.append(f"verses: {exc}")

    for i, item in enumerate(entry.get("tafsir", []), 1):
        missing = [f for f in TAFSIR_FIELDS if not str(item.get(f) or "").strip()]
        if missing:
            errors.append(f"التفسير {i}: حقول ناقصة {missing}")
        elif not _domain_ok(item["url"], sources["tafsir_domains"]):
            errors.append(f"التفسير {i}: رابط خارج مصادر التفسير المعتمدة: {item['url']}")
        if ORNATE_BRACKETS.search(item.get("summary", "")):
            errors.append(f"التفسير {i}: نص آية مكتوب في الخلاصة")

    for i, item in enumerate(entry.get("hadiths", []), 1):
        missing = [f for f in HADITH_FIELDS if not str(item.get(f) or "").strip()]
        if missing:
            errors.append(f"الحديث {i}: الحقول الستة إلزامية، والناقص {missing}")
            continue
        if not _domain_ok(item["url"], sources["domains"]):
            errors.append(f"الحديث {i}: رابط خارج القائمة المعتمدة: {item['url']}")
        weak = any(word in item["grade"] for word in WEAK_GRADES)
        if weak and item.get("purpose") != "show_weakness":
            errors.append(f"الحديث {i}: درجته «{item['grade']}» فلا يُعرض إلا بغرض show_weakness")

    for i, item in enumerate(entry.get("science", []), 1):
        missing = [f for f in SCIENCE_FIELDS if not str(item.get(f) or "").strip()]
        if missing:
            errors.append(f"المعلومة العلمية {i}: حقول ناقصة {missing}")
            continue
        if item["degree"] not in DEGREES:
            errors.append(f"المعلومة العلمية {i}: الدرجة {item['degree']!r} ليست من {DEGREES}")
        if not _domain_ok(item["url"], sources["domains"]):
            errors.append(f"المعلومة العلمية {i}: رابط خارج القائمة المعتمدة: {item['url']}")

    if entry.get("status") == "approved":
        review = entry.get("review") or {}
        if not review.get("reviewer"):
            errors.append("معتمد دون اسم المراجع")
        try:
            date.fromisoformat(review.get("reviewed_at", ""))
        except ValueError:
            errors.append("معتمد دون تاريخ مراجعة صالح")
        if review.get("approved_hash") != approved_hash(entry):
            errors.append("تغيّر محتوى المدخل بعد اعتماده: يحتاج اعتماداً جديداً")

    variants = entry.get("variants", [])
    if len(variants) < 3:
        warnings.append(f"صيغ السؤال {len(variants)}، والمطلوب 3 فأكثر منها صيغة عامية")
    all_text = " ".join(str(entry.get(f) or "") for f in TEXT_FIELDS)
    for phrase in WARN_PHRASES:
        if phrase in all_text:
            warnings.append(f"عبارة تحتاج نظراً: «{phrase}»")
    if ("قال رسول الله" in all_text or "ﷺ" in all_text) and not entry.get("hadiths"):
        warnings.append("ذكر للنبي ﷺ أو لقوله دون حديث مسجّل")
    word_count = len((entry.get("body") or "").split())
    if word_count > 250:
        warnings.append(f"نص الإجابة {word_count} كلمة، والحد المقترح 250")
    return errors, warnings


def read_all(path: Path = KB_PATH) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_all(entries: list[dict], path: Path = KB_PATH) -> None:
    entries = sorted(entries, key=lambda e: e["id"])
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def check_all(entries: list[dict]) -> dict[str, tuple[list[str], list[str]]]:
    results = {}
    seen = set()
    ids = {e.get("id") for e in entries}
    for entry in entries:
        errors, warnings = validate_entry(entry)
        if entry.get("id") in seen:
            errors.append("المعرّف مكرر")
        seen.add(entry.get("id"))
        for rel in entry.get("related", []):
            if rel not in ids:
                errors.append(f"مدخل مرتبط غير موجود: {rel}")
        results[entry.get("id", "?")] = (errors, warnings)
    return results


def approved_only(entries: list[dict]) -> list[dict]:
    """G1: only approved entries whose approval hash still matches their visible content."""
    return [e for e in entries if e.get("status") == "approved"
            and (e.get("review") or {}).get("approved_hash") == approved_hash(e)]


def load_approved(path: Path = KB_PATH) -> list[dict]:
    """Approved entries for serving. Raises KBError if any approved entry breaks a blocking rule."""
    entries = read_all(path)
    problems = []
    for entry_id, (errors, _) in check_all(entries).items():
        entry = next(e for e in entries if e.get("id") == entry_id)
        if entry.get("status") == "approved" and errors:
            problems.append(f"{entry_id}: " + "؛ ".join(errors))
    if problems:
        raise KBError("مداخل معتمدة مخالفة للقواعد:\n" + "\n".join(problems))
    return approved_only(entries)


def kb_hash(entries: list[dict]) -> str | None:
    if not entries:
        return None
    joined = "".join(e["review"]["approved_hash"] for e in sorted(entries, key=lambda e: e["id"]))
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def preview_drafts(path: Path = KB_PATH) -> list[dict]:
    """Team-only local preview: valid drafts treated as approved in memory. Nothing is written."""
    out = []
    for entry in read_all(path):
        if entry.get("status") == "rejected" or validate_entry(entry)[0]:
            continue
        e = json.loads(json.dumps(entry))
        e["status"] = "approved"
        e["review"] = {"reviewer": "معاينة مسودة", "reviewed_at": "2026-10-04", "note": "",
                       "approved_hash": approved_hash(e)}
        out.append(e)
    return out
