"""Automatic checks on answer texts from any system (ours, lexical, plain model, named assistant).

    uv run python eval/auto_checks.py eval/private/runs/<run>.texts.jsonl [eval/private/named/<file>.jsonl ...]

Counts, per system: misquoted verses (text in ﴿﴾ or after «قال تعالى» that is close to a verse but not it; an exact
copy of the mushaf is never counted), hadith mentions without a source or grade in the same or the next paragraph, unsupported scientific certainty («أثبت العلم»، «سبق القرآن»،
«الإعجاز العلمي»), and answers with at least one link (traceability). Prints numbers only, no answer text.
"""
import json
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import quran  # noqa: E402

BRACKETED = re.compile(r"﴿([^﴾]{8,})﴾")
AFTER_SIGNAL = re.compile(r"(?:قال تعالى|قوله تعالى|يقول الله)[:\s«]*([^.؟!\n»]{8,120})")
# A quoted or reported saying, not any mention of the Prophet ﷺ (such as «أخلاق النبي ﷺ»).
# («الحديث» alone also means «the conversation», so it counts only as «في الحديث/في حديث» or before a quote.)
HADITH_MARK = re.compile(r"قال رسول الله|قال النبي|يقول النبي|يقول رسول الله|في الحديث|في حديث|حديث «")
SOURCE_NEAR = re.compile(r"رواه|أخرجه|متفق عليه|البخاري|مسلم|الترمذي|أبو داود|النسائي|ابن ماجه|أحمد|صحيح|حسن|ضعيف|"
                         r"الدرجة|موسوعة الأحاديث|الدرر السنية|hadeethenc|dorar")
NEGATION = re.compile(r"(?:^|\s)(?:لا|لم|ليس|ليست|ولا|فلا|ما|دون|بلا)(?:\s|$)")
CERTAINTY = ("أثبت العلم", "العلم أثبت", "سبق القرآن", "الإعجاز العلمي", "حقيقة علمية قطعية")
LINK = re.compile(r"https?://")


@lru_cache(maxsize=1)
def _mushaf_text() -> str:
    """The whole mushaf in display script, verses joined by a space, for exact-copy checks."""
    return " ".join(v.text for _, v in sorted(quran.verses().items()))


def check_text(text: str) -> dict:
    misquotes = 0
    for snippet in BRACKETED.findall(text) + AFTER_SIGNAL.findall(text):
        if snippet.strip() in _mushaf_text():  # an exact copy of the mushaf (Uthmani script) is not a misquote
            continue
        if quran.find_misquote(f"قال تعالى «{snippet}»") is not None:
            misquotes += 1
    unsourced, last = 0, -1000
    for m in HADITH_MARK.finditer(text):
        if m.start() - last < 60:  # «قال النبي ﷺ» is one mention, not two
            continue
        last = m.start()
        # The mention's own paragraph and the next one: a source line after a long quoted hadith still counts.
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.end())
        if end != -1:
            end = text.find("\n", end + 1)
        window = text[start:] if end == -1 else text[start:end]
        if not SOURCE_NEAR.search(window):
            unsourced += 1
    return {"misquoted_verses": misquotes, "unsourced_hadith": unsourced,
            "certainty_claims": certainty_claims(text), "has_link": bool(LINK.search(text))}


def certainty_claims(text: str) -> int:
    """«أثبت العلم» and the like, unless denied just before («لا ندّعي أن العلم أثبته»، «ليس من الإعجاز العلمي»)."""
    count = 0
    for phrase in CERTAINTY:
        for m in re.finditer(re.escape(phrase), text):
            if not NEGATION.search(text[max(0, m.start() - 25): m.start()]):
                count += 1
    return count


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
