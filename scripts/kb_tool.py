"""Knowledge-base tool: add drafts, check rules, record the reviewer's decisions, build the review digest.

    uv run python scripts/kb_tool.py check
    uv run python scripts/kb_tool.py add drafts.json            # adds or replaces drafts (status: draft)
    uv run python scripts/kb_tool.py approve ID [ID ...] --date 2026-10-04
    uv run python scripts/kb_tool.py status ID needs_edit --note "..."
    uv run python scripts/kb_tool.py digest -o private/digest.html [--all]

Source excerpts for the digest live in private/excerpts/<id>.txt (never committed).
"""
import argparse
import json
import sys
from datetime import date
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import kb, quran  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXCERPTS = ROOT / "private" / "excerpts"
REVIEWER = "المراجع الشرعي للفريق"


def cmd_check(args) -> int:
    entries = kb.read_all()
    results = kb.check_all(entries)
    failed = 0
    for entry_id, (errors, warnings) in results.items():
        status = next(e.get("status") for e in entries if e.get("id") == entry_id)
        mark = "✗" if errors else "✓"
        print(f"{mark} {entry_id} [{status}]")
        for e in errors:
            print(f"    خطأ: {e}")
        for w in warnings:
            print(f"    تنبيه: {w}")
        failed += bool(errors)
    approved = kb.approved_only(entries)
    print(f"\n{len(entries)} مدخلاً، المعتمد منها {len(approved)}، وفيها أخطاء {failed}")
    return 1 if failed else 0


def cmd_add(args) -> int:
    drafts = json.loads(Path(args.file).read_text(encoding="utf-8"))
    entries = {e["id"]: e for e in kb.read_all()}
    for draft in drafts:
        try:
            kb.normalize_refs(draft)
        except ValueError as exc:
            print(f"✗ {draft.get('id')}: {exc}")
            return 1
        draft["status"] = "draft"
        draft.pop("review", None)
        replaced = draft["id"] in entries
        entries[draft["id"]] = draft
        print(("↻ استُبدل " if replaced else "+ أُضيف ") + draft["id"])
    kb.write_all(list(entries.values()))
    return cmd_check(args)


def cmd_approve(args) -> int:
    entries = kb.read_all()
    by_id = {e["id"]: e for e in entries}
    for entry_id in args.ids:
        entry = by_id.get(entry_id)
        if entry is None:
            print(f"✗ غير موجود: {entry_id}")
            return 1
        entry["status"] = "approved"
        entry["review"] = {"reviewer": REVIEWER, "reviewed_at": args.date, "note": args.note or "",
                           "approved_hash": kb.approved_hash(entry)}
        errors, _ = kb.validate_entry(entry)
        if errors:
            print(f"✗ {entry_id} لا يُعتمد قبل إصلاح: " + "؛ ".join(errors))
            return 1
        print(f"✓ اعتُمد {entry_id}")
    kb.write_all(entries)
    return 0


def cmd_status(args) -> int:
    entries = kb.read_all()
    entry = next((e for e in entries if e["id"] == args.id), None)
    if entry is None:
        print(f"✗ غير موجود: {args.id}")
        return 1
    entry["status"] = args.status
    entry["review"] = {"reviewer": REVIEWER, "reviewed_at": args.date, "note": args.note or "", "approved_hash": ""}
    kb.write_all(entries)
    print(f"{args.id} ← {args.status}")
    return 0


DEGREE_LABELS = {"fact": "حقيقة ثابتة", "leading_theory": "نظرية راجحة", "hypothesis": "فرضية"}
LEVEL_LABELS = {"A": "أ: معلومة ثابتة", "B": "ب: شرح واستدلال", "C": "ج: خلافي أو حساس"}


def _expand(text: str) -> str:
    def verse(match):
        ref = match.group(1)
        body = " ".join(v.text for v in quran.lookup(ref))
        return f"﴿{body}﴾ [{quran.label(ref)}]"
    return kb.VERSE_PLACEHOLDER.sub(verse, text or "")


def _paras(text: str) -> str:
    return "".join(f"<p>{escape(line)}</p>" for line in _expand(text).splitlines() if line.strip())


def _entry_html(entry: dict) -> str:
    errors, warnings = kb.validate_entry(entry)
    parts = [f"<section class='entry'><h2>{escape(entry['id'])}</h2>",
             f"<p class='meta'>المستوى {escape(LEVEL_LABELS.get(entry.get('level'), '?'))} · الحالة {escape(entry.get('status', ''))}"
             + (" · صيغت المسودة بمساعدة الذكاء الاصطناعي" if entry.get("drafted_with_ai") else "") + "</p>",
             f"<h3>السؤال</h3><p><b>{escape(entry.get('question', ''))}</b></p>"]
    if entry.get("variants"):
        parts.append("<p class='muted'>صيغ أخرى: " + " · ".join(escape(v) for v in entry["variants"]) + "</p>")
    parts.append("<h3>الخلاصة</h3>" + _paras(entry.get("summary")))
    if entry.get("explain_simple"):
        parts.append("<h3>شرح مبسّط</h3>" + _paras(entry["explain_simple"]))
    parts.append("<h3>الإجابة الكاملة</h3>" + _paras(entry.get("body")))
    if entry.get("verses"):
        parts.append("<h3>الآيات (من ملف المصحف)</h3>")
        for ref in entry["verses"]:
            body = " ".join(v.text for v in quran.lookup(ref))
            parts.append(f"<p class='verse'>﴿{escape(body)}﴾ [{escape(quran.label(ref))}]</p>")
    for item in entry.get("tafsir", []):
        parts.append(f"<h3>التفسير: {escape(item.get('mufassir', ''))}</h3>{_paras(item.get('summary'))}"
                     f"<p class='muted'>المصدر: {escape(item.get('source', ''))} · <a href='{escape(item.get('url', ''))}'>الرابط</a></p>")
    for item in entry.get("hadiths", []):
        parts.append(f"<h3>حديث</h3><p>{escape(item.get('text', ''))}</p><p class='muted'>{escape(item.get('source', ''))}، رقم "
                     f"{escape(str(item.get('number', '')))} · الدرجة: {escape(item.get('grade', ''))} · المحدّث: "
                     f"{escape(item.get('grader', ''))} · <a href='{escape(item.get('url', ''))}'>الرابط</a></p>")
    for item in entry.get("science", []):
        parts.append(f"<h3>معلومة علمية ({escape(DEGREE_LABELS.get(item.get('degree'), '?'))})</h3>"
                     f"<p>{escape(item.get('claim', ''))}</p><p class='muted'>{escape(item.get('source', ''))} · "
                     f"<a href='{escape(item.get('url', ''))}'>الرابط</a></p>")
    source = entry.get("source") or {}
    parts.append(f"<h3>المصدر</h3><p>{escape(source.get('name', ''))} {escape(source.get('locator', ''))} · "
                 f"<a href='{escape(source.get('url', ''))}'>الرابط</a></p>")
    excerpt = EXCERPTS / f"{entry['id']}.txt"
    if excerpt.exists():
        parts.append("<h3>مقتطف المصدر (للمطابقة)</h3><div class='excerpt'>"
                     + _paras(excerpt.read_text(encoding="utf-8")) + "</div>")
    if entry.get("review_notes"):
        parts.append("<h3>نقاط تحتاج قرارك</h3><div class='warn'>"
                     + "".join(f"<p>• {escape(n)}</p>" for n in entry["review_notes"]) + "</div>")
    if errors or warnings:
        parts.append("<div class='warn'>" + "".join(f"<p>⚠ {escape(m)}</p>" for m in errors + warnings) + "</div>")
    parts.append(f"<p class='reply'>للرد: «{escape(entry['id'])}: معتمد» أو «{escape(entry['id'])}: يحتاج تعديل، ...» أو «مرفوض»</p></section>")
    return "\n".join(parts)


DIGEST_CSS = """
body{font-family:system-ui,'Segoe UI',Tahoma,sans-serif;font-size:17px;line-height:1.8;margin:0;padding:16px;background:#f7f5f0;color:#1d2321}
h1{font-size:1.3rem}h2{font-size:1.15rem;margin:0 0 4px;direction:ltr;text-align:right}h3{font-size:1rem;margin:16px 0 4px;color:#1f5f4a}
.entry{background:#fff;border:1px solid #d9d5cb;border-radius:12px;padding:12px 16px;margin-bottom:20px}
.meta,.muted{color:#55605b;font-size:.9rem}.verse{font-size:1.15rem}.excerpt{background:#eef3f0;border-radius:8px;padding:8px 12px}
.warn{background:#fff6dc;border-radius:8px;padding:8px 12px;margin-top:12px}.reply{margin-top:12px;font-size:.9rem;color:#55605b}
"""


def cmd_digest(args) -> int:
    if args.source:
        entries = []
        for path in sorted(Path(args.source).glob("*.json")) if Path(args.source).is_dir() else [Path(args.source)]:
            for e in json.loads(path.read_text(encoding="utf-8")):
                kb.normalize_refs(e)
                entries.append(e)
    else:
        entries = kb.read_all()
    if not args.all and not args.source:
        entries = [e for e in entries if e.get("status") in ("draft", "needs_edit")]
    entries.sort(key=lambda e: (not e.get("critical", False), e["id"]))
    body = "\n".join(_entry_html(e) for e in entries) or "<p>لا مسودات.</p>"
    html = (f"<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>{escape(args.title)}</title>"
            f"<style>{DIGEST_CSS}</style></head><body><h1>{escape(args.title)} ({len(entries)})</h1>"
            f"<p class='muted'>الآيات معروضة من ملف المصحف بمرجعها. المطلوب لكل إجابة: معتمد، أو يحتاج تعديل مع الملاحظة، أو مرفوض.</p>"
            f"{body}</body></html>")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"{out} ({len(entries)} إجابة)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("add")
    p.add_argument("file")
    p = sub.add_parser("approve")
    p.add_argument("ids", nargs="+")
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--note")
    p = sub.add_parser("status")
    p.add_argument("id")
    p.add_argument("status", choices=["draft", "needs_edit", "rejected"])
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--note")
    p = sub.add_parser("digest")
    p.add_argument("-o", "--output", default=str(ROOT / "private" / "digest.html"))
    p.add_argument("--all", action="store_true")
    p.add_argument("--source", help="a drafts JSON file or a folder of them, instead of content/kb.json")
    p.add_argument("--title", default="مراجعة الإجابات")
    args = parser.parse_args()
    return {"check": cmd_check, "add": cmd_add, "approve": cmd_approve,
            "status": cmd_status, "digest": cmd_digest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
