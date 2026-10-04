"""G5: the generated framing sentence must carry no claim. Any failed condition drops it silently.

The eight conditions (build-plan §6). The last one is a whitelist: every content word must come from the
user's own message or from the approved neutral connector lexicon, so the model cannot add a ruling,
a source, a fact or a scientific term of its own.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from app import arabic, quran

LEXICON_PATH = Path(__file__).resolve().parent.parent / "content" / "framing_lexicon.json"
MIN_CHARS, MAX_CHARS, MAX_SENTENCES = 15, 160, 2
_FORBIDDEN_CHARS = re.compile(r"[0-9٠-٩۰-۹A-Za-z﴾﴿«»\"“”()]|https?:|www\.")
_SENTENCE_END = re.compile(r"[.!?؟]+")
_PREFIXES = ("وال", "فال", "بال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ك")


@lru_cache(maxsize=1)
def _lexicon() -> tuple[frozenset[str], tuple[str, ...]]:
    data = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    connectors = frozenset(arabic.normalize(w) for w in data["connectors"])
    blocklist = tuple(arabic.normalize(w) for w in data["blocklist"])
    return connectors, blocklist


@lru_cache(maxsize=1)
def _mushaf_fourgrams() -> frozenset[tuple[str, ...]]:
    grams = set()
    for verse in quran.verses().values():
        words = arabic.words(verse.plain)
        grams.update(tuple(words[i:i + 4]) for i in range(len(words) - 3))
    return frozenset(grams)


def _stems(word: str) -> set[str]:
    """The word and its forms without one attached prefix (و، ف، ب، ل، ك، ال)."""
    forms = {word}
    for prefix in _PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= 2:
            forms.add(word[len(prefix):])
    return forms


def check_framing(framing: str, message: str, source_names: list[str], hadith_texts: list[str]) -> bool:
    text = (framing or "").strip()
    # 1. Length and sentence count.
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        return False
    if len([s for s in _SENTENCE_END.split(text) if s.strip()]) > MAX_SENTENCES:
        return False
    # 2. No digits, Latin letters, links, quotation marks, ornate brackets or parentheses.
    if _FORBIDDEN_CHARS.search(text):
        return False
    words = arabic.words(text)
    joined = " " + " ".join(words) + " "
    connectors, blocklist = _lexicon()
    # 3 and 4. No blocked word (rulings, attribution, certainty, «شك», «وسواس»), with or without a prefix.
    for blocked in blocklist:
        if " " in blocked:
            if f" {blocked} " in joined:
                return False
        elif any(blocked in _stems(w) for w in words):
            return False
    # 5. No source name from the knowledge base.
    for name in source_names:
        if name and arabic.normalize(name) in arabic.normalize(text):
            return False
    # 6. No four consecutive words from the mushaf or from hadith texts.
    hadith_grams = set()
    for h in hadith_texts:
        hw = arabic.words(h)
        hadith_grams.update(tuple(hw[i:i + 4]) for i in range(len(hw) - 3))
    for i in range(len(words) - 3):
        gram = tuple(words[i:i + 4])
        if gram in _mushaf_fourgrams() or gram in hadith_grams:
            return False
    # 7 and 8. Whitelist: every word comes from the user's message or the connector lexicon,
    # so scientific topic names appear only if the user wrote them.
    allowed = set(connectors)
    for w in arabic.words(message):
        allowed |= _stems(w)
    return all(_stems(w) & allowed for w in words)
