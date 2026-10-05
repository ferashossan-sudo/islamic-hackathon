"""Knowledge-base tool: add drafts, check rules, record the reviewer's decisions, build the review digest.

    uv run python scripts/kb_tool.py check
    uv run python scripts/kb_tool.py add drafts.json            # adds or replaces drafts (status: draft)
    uv run python scripts/kb_tool.py approve ID [ID ...] --date 2026-10-04
    uv run python scripts/kb_tool.py status ID needs_edit --note "..."
    uv run python scripts/kb_tool.py digest -o private/digest.html [--all]
    uv run python scripts/kb_tool.py decisions private/drafts/batch2 private/drafts/batch3 -o private/decisions.html
    uv run python scripts/kb_tool.py reasoning-digest --ids ID,ID --out private/reasoning.html   # or --all-with-reasoning
    uv run python scripts/kb_tool.py merge-reasoning staging/*.json   # files of {"id": ..., "reasoning": {...}}

Source excerpts for the digest live in private/excerpts/<id>.txt (never committed).
"""
import argparse
import copy
import json
import sys
from datetime import date
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import kb, quran, texts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXCERPTS = ROOT / "private" / "excerpts"
DRAFT_DIRS = ROOT / "private" / "drafts"  # team-only drafts awaiting review (as in eval/run_eval.py)
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
        before = entry.get("review") or {}
        entry["status"] = "approved"
        entry["review"] = {"reviewer": REVIEWER, "reviewed_at": args.date, "note": args.note or "",
                           "approved_hash": kb.approved_hash(entry)}
        # The «بالعقل والعلم» layer keeps its own approval: approved here only with --with-reasoning,
        # and an earlier approval of it survives while the layer is unchanged.
        if args.with_reasoning and entry.get("reasoning") is not None:
            entry["review"]["reasoning_hash"] = kb.reasoning_hash(entry)
            entry["review"]["reasoning_reviewed_at"] = args.date
        elif before.get("reasoning_hash") and before.get("reasoning_hash") == kb.reasoning_hash(entry):
            entry["review"]["reasoning_hash"] = before["reasoning_hash"]
            entry["review"]["reasoning_reviewed_at"] = before.get("reasoning_reviewed_at", "")
        errors, _ = kb.validate_entry(entry)
        if errors:
            print(f"✗ {entry_id} لا يُعتمد قبل إصلاح: " + "؛ ".join(errors))
            return 1
        print(f"✓ اعتُمد {entry_id}")
    kb.write_all(entries)
    return 0


def approve_reasoning(ids: list[str], on: str, kb_path: Path) -> int:
    """The reviewer approved the «بالعقل والعلم» layer of these entries (in content/kb.json) as it stands now."""
    entries = kb.read_all(kb_path)
    by_id = {e["id"]: e for e in entries}
    for entry_id in ids:
        entry = by_id.get(entry_id)
        if entry is None or entry.get("reasoning") is None:
            print(f"✗ {entry_id}: غير موجود في kb.json أو بلا طبقة «بالعقل والعلم»")
            return 1
        errors = [m for m in kb.validate_entry(kb.normalize_refs(copy.deepcopy(entry)))[0]
                  if m.startswith("العقل والعلم")]
        if errors:
            print(f"✗ {entry_id} لا تُعتمد طبقته قبل إصلاح: " + "؛ ".join(errors))
            return 1
        review = entry.setdefault("review", {})
        review["reasoning_hash"] = kb.reasoning_hash(entry)
        review["reasoning_reviewed_at"] = on
        print(f"✓ اعتُمدت طبقة العقل والعلم: {entry_id}")
    kb.write_all(entries, kb_path)
    return 0


def cmd_approve_reasoning(args) -> int:
    return approve_reasoning(args.ids, args.date, kb.KB_PATH)


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


REASONING_HEADING = "بالعقل والعلم (جديد)"


def _source_link(item: dict) -> str:
    name = " ".join(x for x in (str(item.get("source") or ""), str(item.get("locator") or "")) if x)
    return f"{escape(name)} · <a href='{escape(str(item.get('url') or ''))}'>الرابط</a>"


def _reasoning_html(entry: dict) -> str:
    """The «بالعقل والعلم» layer for the sharia reviewer: the steps in order, then the objections with answers."""
    reasoning = entry.get("reasoning")
    if not isinstance(reasoning, dict):
        return ""
    steps = [s for s in reasoning.get("steps") or [] if isinstance(s, dict)]
    objections = [o for o in reasoning.get("objections") or [] if isinstance(o, dict)]
    labels = texts.pairs("ui_labels")
    parts = [f"<div class='reasoning'><h3>{escape(REASONING_HEADING)}</h3>",
             "<p class='muted'>سلسلة استدلال بالعقل والعلم، بلا آيات ولا أحاديث، تُعرض تحت الإجابة ويبني عليها "
             "المساعد حواره ونقاشه.</p><ol>"]
    for s in steps:
        basis = labels.get(f"basis_{s.get('basis')}", f"أساس غير معروف: {s.get('basis')}")
        parts.append(f"<li><p>{escape(str(s.get('text') or ''))}</p>"
                     f"<p class='muted'>{escape(basis)} · {_source_link(s)}</p></li>")
    parts.append("</ol>")
    if objections:
        parts.append(f"<h4>{escape(labels['objections_title'])}</h4>")
        for o in objections:
            parts.append(f"<div class='objection'><p><b>الاعتراض: {escape(str(o.get('objection') or ''))}</b></p>"
                         f"<p>الجواب: {escape(str(o.get('response') or ''))}</p>"
                         f"<p class='muted'>{_source_link(o)}</p></div>")
    parts.append("</div>")
    return "".join(parts)


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
    parts.append(_reasoning_html(entry))
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
.reasoning{border:2px dashed #1f5f4a;border-radius:10px;padding:4px 14px;margin-top:16px;background:#f4faf7}
.reasoning h4{margin:12px 0 4px;font-size:.95rem}.reasoning ol{padding-inline-start:22px}
.objection{border-inline-start:3px solid #d9d5cb;padding-inline-start:10px;margin:8px 0}
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


# --- «بالعقل والعلم»: a short review addendum, and merging staged reasoning into the entries ---

def _load_file(path: Path) -> tuple[list[dict], dict]:
    """A draft file's entries and how it is written (line endings, final newline), so it is written back alike."""
    with open(path, encoding="utf-8", newline="") as f:
        raw = f.read()
    return json.loads(raw), {"crlf": "\r\n" in raw, "final_newline": raw.endswith("\n")}


def _write_file(path: Path, entries: list[dict], style: dict) -> None:
    text = json.dumps(entries, ensure_ascii=False, indent=2) + ("\n" if style["final_newline"] else "")
    if style["crlf"]:
        text = text.replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _errors(entry: dict) -> list[str]:
    """Blocking errors of an entry checked as eval/run_eval.py load_drafts checks it (on a normalized copy)."""
    try:
        e = kb.normalize_refs(copy.deepcopy(entry))
    except ValueError as exc:
        return [str(exc)]
    return kb.validate_entry(e)[0]


def _copies(kb_path: Path, drafts_dir: Path) -> list[tuple[Path, int, dict]]:
    """Every copy of every entry, in the order load_drafts reads them: content/kb.json, then the draft files."""
    out = [(kb_path, i, e) for i, e in enumerate(kb.read_all(kb_path))]
    for path in sorted(drafts_dir.glob("*/*.json")):
        out += [(path, i, e) for i, e in enumerate(_load_file(path)[0])]
    return out


def _locate(entry_id: str, kb_path: Path, drafts_dir: Path) -> tuple[Path | None, int, list[str]]:
    """The copy that load_drafts serves: the last valid one (content/kb.json for batch 1, else the draft files).

    Validity ignores the copy's current reasoning, which is the part being replaced. (None, -1, problems) if none."""
    problems = []
    for path, index, e in reversed(_copies(kb_path, drafts_dir)):
        if e.get("id") != entry_id:
            continue
        errors = ["rejected"] if e.get("status") == "rejected" else _errors(
            {k: v for k, v in e.items() if k != "reasoning"})
        if not errors:
            return path, index, []
        problems.append(f"{_show(path)}: {errors[0]}")
    return None, -1, problems or ["غير موجود في content/kb.json ولا في private/drafts"]


def _show(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def merge_reasoning(files: list[str], kb_path: Path, drafts_dir: Path) -> int:
    """Write each staged {"id", "reasoning"} into its entry; an entry that then fails validation is not written."""
    failed = 0
    for name in files:
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"✗ {name}: لا يُقرأ ملفاً بصيغة JSON: {exc}")
            failed += 1
            continue
        for item in data if isinstance(data, list) else [data]:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or "reasoning" not in item
                    or set(item) - {"id", "reasoning"}):
                print(f'✗ {name}: الصيغة المطلوبة {{"id": "...", "reasoning": {{...}}}} دون حقول أخرى')
                failed += 1
                continue
            entry_id = item["id"]
            path, index, problems = _locate(entry_id, kb_path, drafts_dir)
            if path is None:
                print(f"✗ {entry_id}: لا نسخة صالحة يُدمج فيها: " + "؛ ".join(problems))
                failed += 1
                continue
            is_kb = path == kb_path
            entries, style = (kb.read_all(kb_path), None) if is_kb else _load_file(path)
            updated = {**entries[index], "reasoning": item["reasoning"]}
            errors = _errors(updated)
            if errors:
                print(f"✗ {entry_id}: لم يُكتب، لأن المدخل بعد الدمج لا يجتاز التحقق ({_show(path)}):")
                for e in errors:
                    print(f"    خطأ: {e}")
                failed += 1
                continue
            entries[index] = updated
            if is_kb:
                kb.write_all(entries, kb_path)
            else:
                _write_file(path, entries, style)
            print(f"✓ {entry_id} ← {_show(path)}")
            for w in kb.validate_entry(kb.normalize_refs(copy.deepcopy(updated)))[1]:
                print(f"    تنبيه: {w}")
    return 1 if failed else 0


def cmd_merge_reasoning(args) -> int:
    return merge_reasoning(args.files, kb.KB_PATH, DRAFT_DIRS)


def _reasoning_section(entry: dict) -> str:
    e = kb.normalize_refs(copy.deepcopy(entry))
    errors, warnings = kb.validate_entry(e)
    own = [m for m in errors + warnings if m.startswith("العقل والعلم")]
    parts = [f"<section class='entry'><h2>{escape(entry['id'])}</h2>",
             f"<p class='meta'>المستوى {escape(LEVEL_LABELS.get(entry.get('level'), '?'))} · "
             f"الحالة {escape(entry.get('status', ''))}</p>",
             f"<h3>السؤال</h3><p><b>{escape(_expand(entry.get('question', '')))}</b></p>",
             _reasoning_html(entry) or "<p class='muted'>لا طبقة «بالعقل والعلم» لهذا المدخل بعد.</p>"]
    if own:
        parts.append("<div class='warn'>" + "".join(f"<p>⚠ {escape(m)}</p>" for m in own) + "</div>")
    parts.append(f"<p class='reply'>للرد: «{escape(entry['id'])}: العقل والعلم معتمد» أو "
                 f"«{escape(entry['id'])}: العقل والعلم يحتاج تعديل، ...»</p></section>")
    return "\n".join(parts)


def reasoning_digest(ids: list[str] | None, out: Path, title: str, kb_path: Path, drafts_dir: Path) -> int:
    """Only the reasoning layer of the given entries (or of every entry that has one), each with its question."""
    explicit = ids is not None
    if not explicit:
        ids = sorted({e["id"] for _, _, e in _copies(kb_path, drafts_dir) if e.get("id") and e.get("reasoning")})
    sections, missing = [], 0
    for entry_id in ids:
        path, index, problems = _locate(entry_id, kb_path, drafts_dir)
        if path is None:
            print(f"✗ {entry_id}: " + "؛ ".join(problems))
            missing += 1
            continue
        entry = (kb.read_all(kb_path) if path == kb_path else _load_file(path)[0])[index]
        if not explicit and entry.get("reasoning") is None:
            continue  # an earlier copy had a reasoning layer; the copy that is served has none
        sections.append(_reasoning_section(entry))
    html = (f"<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>{escape(title)}</title>"
            f"<style>{DIGEST_CSS}</style></head><body><h1>{escape(title)} ({len(sections)})</h1>"
            f"<p class='muted'>ملحق قصير: طبقة «بالعقل والعلم» وحدها. خطوات استدلال بالعقل والعلم بلا آيات ولا أحاديث، "
            f"ثم اعتراضات شائعة وجوابها، ولكل منها مصدره. يبني عليها المساعد حواره حين يطلب السائل الإقناع "
            f"بالعقل أو يعترض. المطلوب لكل إجابة: معتمد، أو يحتاج تعديل مع الملاحظة.</p>"
            f"{''.join(sections) or '<p>لا مداخل فيها هذه الطبقة.</p>'}</body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"{out} ({len(sections)} إجابة)")
    return 1 if missing else 0


def cmd_reasoning_digest(args) -> int:
    ids = None if args.all_with_reasoning else [i.strip() for i in args.ids.split(",") if i.strip()]
    return reasoning_digest(ids, Path(args.out), args.title, kb.KB_PATH, DRAFT_DIRS)


OWNERS = {"[لقائد الفريق]": "lead", "[للمراجع الشرعي]": "sharia"}
DECISIONS_JS = """
const KEY = "ltq-decisions";
let saved = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { saved = {}; }
const NL = String.fromCharCode(10);
function store() { try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) {} }
function compile() {
  const lines = [];
  document.querySelectorAll(".item").forEach((item) => {
    const s = saved[item.dataset.id];
    if (s && (s.choice || s.note)) lines.push(item.dataset.id + ": " + (s.choice || "") + (s.note ? " — " + s.note : ""));
  });
  document.getElementById("out").value = lines.join(NL);
  document.getElementById("count").textContent = lines.length;
}
document.querySelectorAll(".item").forEach((item) => {
  const id = item.dataset.id;
  const s = saved[id] || {};
  item.querySelectorAll("button[data-choice]").forEach((b) => {
    if (s.choice === b.dataset.choice) b.classList.add("on");
    b.addEventListener("click", () => {
      saved[id] = Object.assign(saved[id] || {}, { choice: b.dataset.choice });
      item.querySelectorAll("button[data-choice]").forEach((x) => x.classList.toggle("on", x === b));
      store(); compile();
    });
  });
  const note = item.querySelector("input");
  note.value = s.note || "";
  note.addEventListener("input", () => { saved[id] = Object.assign(saved[id] || {}, { note: note.value }); store(); compile(); });
});
document.getElementById("all").addEventListener("click", () => {
  document.querySelectorAll(".item").forEach((item) => {
    const id = item.dataset.id;
    if (!(saved[id] && saved[id].choice)) {
      saved[id] = Object.assign(saved[id] || {}, { choice: "موافق" });
      item.querySelector("button[data-choice]").classList.add("on");
    }
  });
  store(); compile();
});
document.getElementById("copy").addEventListener("click", () => {
  const out = document.getElementById("out");
  out.select();
  try { navigator.clipboard.writeText(out.value); } catch (e) { document.execCommand("copy"); }
});
compile();
"""
DECISIONS_CSS = DIGEST_CSS + """
.item{border-top:1px solid #dde3df;padding:10px 0}.q{font-weight:600}.rec{background:#eef3f0;border-radius:8px;padding:6px 10px;margin:6px 0}
.item button{border:1px solid #9aa8a1;background:#fff;border-radius:16px;padding:4px 12px;margin-inline-end:6px;cursor:pointer;font:inherit}
.item button.on{background:#1f6f5c;color:#fff;border-color:#1f6f5c}.item input{width:100%;margin-top:6px;padding:6px;font:inherit}
textarea{width:100%;min-height:120px;font:inherit}.bar{position:sticky;top:0;background:#faf8f3;padding:8px 0;z-index:1}
.tag{display:inline-block;font-size:.8rem;border-radius:10px;padding:0 8px;background:#e8ecea;margin-inline-start:6px}
"""


def _decision_items(entries: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Open review notes split by owner; lead questions about the same thing (OpenStax) are merged."""
    lead, sharia = [], {}
    for e in entries:
        for n, note in enumerate(e.get("review_notes") or [], 1):
            if note.startswith("[قرار"):  # already decided («[قرار قائد الفريق ...]»)
                continue
            owner = next((v for k, v in OWNERS.items() if note.startswith(k)), "sharia")
            text = note
            for k in OWNERS:
                text = text.removeprefix(k).strip()
            question, _, rec = text.partition(" — المقترح:")
            item = {"id": f"{e['id']}#{n}", "entry": e["id"], "title": e["question"], "q": question.strip(),
                    "rec": rec.strip()}
            if owner == "lead":
                same = next((x for x in lead if x["q"] == question.strip()
                             or ("OpenStax" in x["q"] and "OpenStax" in question)), None)
                if same:
                    same["also"].append(e["id"])
                else:
                    lead.append({**item, "also": []})
            else:
                sharia.setdefault(e["id"], []).append(item)
    return lead, sharia


def _item_html(item: dict, context: str = "") -> str:
    rec = f"<div class='rec'>المقترح: {escape(item['rec'])}</div>" if item["rec"] else ""
    return (f"<div class='item' data-id='{escape(item['id'])}'><div class='meta'>{escape(item['id'])}{context}</div>"
            f"<div class='q'>{escape(item['q'])}</div>{rec}"
            f"<button data-choice='موافق'>موافق على المقترح</button><button data-choice='لا'>غير موافق</button>"
            f"<input placeholder='ملاحظة (اختياري)'></div>")


def cmd_decisions(args) -> int:
    entries = [e for e in kb.read_all() if e.get("status") != "approved"]  # batch 1, still in review
    for folder in args.source:
        for path in sorted(Path(folder).glob("*.json")):
            entries.extend(json.loads(path.read_text(encoding="utf-8")))
    lead, sharia = _decision_items(entries)
    lead_html = "".join(_item_html(x, (" · يخص أيضاً: " + "، ".join(x["also"])) if x["also"] else "") for x in lead)
    sharia_html = "".join(
        f"<h3>{escape(items[0]['title'])} <span class='tag'>{escape(eid)}</span></h3>" + "".join(_item_html(x) for x in items)
        for eid, items in sorted(sharia.items()))
    n_sharia = sum(len(v) for v in sharia.values())
    html = (f"<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>{escape(args.title)}</title>"
            f"<style>{DECISIONS_CSS}</style></head><body><h1>{escape(args.title)}</h1>"
            f"<p class='muted'>{len(entries)} مسودة. بقي {len(lead)} قراراً لقائد الفريق و{n_sharia} للمراجع الشرعي. "
            f"اختر لكل نقطة «موافق على المقترح» أو «غير موافق» مع ملاحظة، ثم انسخ القرارات وأرسلها. "
            f"تُحفظ اختياراتك في هذا المتصفح.</p>"
            f"<div class='bar'><button id='all'>موافق على كل المقترحات الباقية</button> "
            f"<button id='copy'>انسخ قراراتي (<span id='count'>0</span>)</button></div>"
            f"<h2>قرارات قائد الفريق ({len(lead)})</h2>{lead_html}"
            f"<h2>قرارات المراجع الشرعي ({n_sharia})</h2>{sharia_html}"
            f"<h2>قراراتك</h2><textarea id='out' readonly></textarea>"
            f"<script>{DECISIONS_JS}</script></body></html>")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"{out} (lead {len(lead)}, sharia {n_sharia})")
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
    p.add_argument("--with-reasoning", action="store_true", help="approve the «بالعقل والعلم» layer too")
    p = sub.add_parser("approve-reasoning", help="approve only the «بالعقل والعلم» layer of entries in kb.json")
    p.add_argument("ids", nargs="+")
    p.add_argument("--date", default=date.today().isoformat())
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
    p = sub.add_parser("decisions")
    p.add_argument("source", nargs="+", help="folders of draft JSON files")
    p.add_argument("-o", "--output", default=str(ROOT / "private" / "decisions.html"))
    p.add_argument("--title", default="ورقة القرارات")
    p = sub.add_parser("reasoning-digest", help="only the «بالعقل والعلم» layer of some entries, for a quick review")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--ids", help="comma-separated entry ids")
    which.add_argument("--all-with-reasoning", action="store_true")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--title", default="مراجعة طبقة «بالعقل والعلم»")
    p = sub.add_parser("merge-reasoning", help='write staged {"id", "reasoning"} files into the entries')
    p.add_argument("files", nargs="+")
    args = parser.parse_args()
    return {"check": cmd_check, "add": cmd_add, "approve": cmd_approve, "approve-reasoning": cmd_approve_reasoning,
            "status": cmd_status, "digest": cmd_digest, "decisions": cmd_decisions,
            "reasoning-digest": cmd_reasoning_digest, "merge-reasoning": cmd_merge_reasoning}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
