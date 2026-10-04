"""G7: distress detection, run before any other step. Deterministic list, no model call.

Sensitivity is deliberate: a false alarm costs a calm card; a miss can cost far more.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from app import arabic, texts

TERMS_PATH = Path(__file__).resolve().parent.parent / "content" / "distress_terms.json"
# Harm by others adds the domestic-violence line (from the approved text's own scope note). It covers the writer
# and the people at home («ابوي يضرب امي»), but not general questions («الرجل يضرب زوجته؟»، «اضربوهن»).
_SELF_HARM_BY_OTHERS = ("يضربني", "يعنفني", "يتحرش بي", "يتحرش فيني", "اعتدي علي", "يهددني", "يؤذيني", "يأذيني",
                        "تضربني", "تعنفني", "تؤذيني", "تهددني", "يضربنا", "يعنفنا", "يؤذينا", "يهددنا", "hits me",
                        "abuses me")
_HARM_VERBS = ("يضرب", "يضربون", "تضرب", "يعنف", "تعنف", "يؤذي", "يأذي", "تؤذي", "يهدد", "تهدد", "يتحرش ب")
_HOME = ("امي", "ماما", "اختي", "اخوي", "اخواني", "اخواتي", "خواتي", "ابوي", "زوجتي", "ولدي", "بنتي", "عيالي",
         "اهلي", "امنا")
HARM_TERMS = _SELF_HARM_BY_OTHERS + tuple(f"{v} {o}" if not v.endswith(" ب") else f"{v}{o}"
                                          for v in _HARM_VERBS for o in _HOME)
_LATIN = re.compile(r"[a-z]")


@lru_cache(maxsize=1)
def _terms() -> tuple[tuple[str, ...], tuple[str, ...]]:
    raw = json.loads(TERMS_PATH.read_text(encoding="utf-8"))["terms"]
    terms = [arabic.normalize(t) for t in raw if t.strip()]
    # Arabic phrases match anywhere (they may carry attached prefixes such as و or ف);
    # short or Latin terms match whole words only.
    anywhere = tuple(t for t in terms if len(t) >= 4 and not _LATIN.search(t))
    whole = tuple(t for t in terms if t not in anywhere)
    return anywhere, whole


def _prepared(message: str) -> str:
    return " " + " ".join(arabic.words(message)) + " "


def detect_distress(message: str) -> bool:
    text = _prepared(message)
    anywhere, whole = _terms()
    return (any(t in text for t in anywhere) or any(f" {t} " in text for t in whole)
            or mentions_harm_by_others(message))


def mentions_harm_by_others(message: str) -> bool:
    text = _prepared(message)
    return any(arabic.normalize(t) in text for t in HARM_TERMS)


def contacts() -> list[dict]:
    """Support numbers from the approved fixed text, one per line: «label: number · note · المصدر: source»."""
    items = []
    for line in texts.text("distress_contacts").splitlines():
        head, _, rest = line.partition(" · ")
        label, _, number = head.rpartition(":")
        parts = [p.strip() for p in rest.split(" · ") if p.strip()]
        source = next((p.removeprefix("المصدر:").strip() for p in parts if p.startswith("المصدر:")), "")
        note = " · ".join(p for p in parts if not p.startswith("المصدر:"))
        items.append({"label": label.strip(), "number": number.strip(), "note": note, "source": source})
    return items
