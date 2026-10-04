"""Automatic checks on answer texts from any system (ours, lexical, plain model, named assistant).

    uv run python eval/auto_checks.py eval/private/runs/<run>.texts.jsonl [eval/private/named/<file>.jsonl ...]

Counts, per system: misquoted verses (text in ﴿﴾ or after «قال تعالى» that is close to a verse but not it),
hadith mentions without a source or grade nearby, unsupported scientific certainty («أثبت العلم»، «سبق القرآن»،
«الإعجاز العلمي»), and answers with at least one link (traceability). Prints numbers only, no answer text.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import quran  # noqa: E402

BRACKETED = re.compile(r"﴿([^﴾]{8,})﴾")
AFTER_SIGNAL = re.compile(r"(?:قال تعالى|قوله تعالى|يقول الله)[:\s«]*([^.؟!\n»]{8,120})")
HADITH_MARK = re.compile(r"قال رسول الله|قال النبي|ﷺ|في الحديث|حديث")
SOURCE_NEAR = re.compile(r"رواه|البخاري|مسلم|الترمذي|أبو داود|النسائي|ابن ماجه|أحمد|صحيح|حسن|ضعيف|الدرجة|hadeethenc|dorar")
CERTAINTY = ("أثبت العلم", "العلم أثبت", "سبق القرآن", "الإعجاز العلمي", "حقيقة علمية قطعية")
LINK = re.compile(r"https?://")


def check_text(text: str) -> dict:
    misquotes = 0
    for snippet in BRACKETED.findall(text) + AFTER_SIGNAL.findall(text):
        if quran.find_misquote(f"قال تعالى «{snippet}»") is not None:
            misquotes += 1
    unsourced, last = 0, -1000
    for m in HADITH_MARK.finditer(text):
        if m.start() - last < 60:  # «قال النبي ﷺ» is one mention, not two
            continue
        last = m.start()
        window = text[max(0, m.start() - 150): m.end() + 250]
        if not SOURCE_NEAR.search(window):
            unsourced += 1
    return {"misquoted_verses": misquotes, "unsourced_hadith": unsourced,
            "certainty_claims": sum(text.count(p) for p in CERTAINTY), "has_link": bool(LINK.search(text))}


def main() -> int:
    totals = defaultdict(lambda: defaultdict(int))
    for path in sys.argv[1:]:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            result = check_text(row["text"])
            t = totals[row.get("system", Path(path).stem)]
            t["answers"] += 1
            for key, value in result.items():
                t[key] += int(value)
    print("| System | Answers | Misquoted verses | Hadith without source/grade | Scientific certainty claims | With a link |")
    print("|---|---|---|---|---|---|")
    for system, t in totals.items():
        print(f"| {system} | {t['answers']} | {t['misquoted_verses']} | {t['unsourced_hadith']} | "
              f"{t['certainty_claims']} | {t['has_link']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
