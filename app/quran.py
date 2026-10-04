"""The mushaf in memory: verses by (sura, aya) from the local Quranpedia dump (G2).

Verse text shown to the user always comes from here, never from a KB entry or the model.
"""
import gzip
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.arabic import normalize

DATA = Path(__file__).resolve().parent.parent / "data" / "quran"
TOTAL_VERSES = 6236

# Quranpedia encodes consecutive tanween with the older KFGQPC code points; v30 uses Unicode 14.
_TANWEEN = str.maketrans({"\u0657": "\u08f0", "\u065e": "\u08f1", "\u0656": "\u08f2"})
_REF = re.compile(r"^\s*(?P<sura>[^\s:]+(?:\s[^\s:]+)?)\s*:\s*(?P<start>\d+)\s*(?:-\s*(?P<end>\d+))?\s*$")


@dataclass(frozen=True)
class Verse:
    sura: int
    aya: int
    text: str  # Uthmani, aligned to KFGQPC v30
    plain: str  # imla'i with diacritics, for matching

    @property
    def ref(self) -> str:
        return f"{self.sura}:{self.aya}"


def _clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text.replace("\ufeff", "")).split())


def _display(text: str) -> str:
    text = unicodedata.normalize("NFC", _clean(text).translate(_TANWEEN))
    return re.sub("ـ+", "ـ", text)


def _load_dump(name: str) -> dict:
    return json.loads(gzip.decompress((DATA / name).read_bytes()))["data"]


@lru_cache(maxsize=1)
def _mushaf() -> tuple[dict[tuple[int, int], Verse], dict[int, str], dict[str, int]]:
    uthmani = _load_dump("mushafs-2.json.gz")
    imlaei = _load_dump("mushafs-1.json.gz")
    alignment = json.loads((DATA / "kfgqpc_alignment.json").read_text(encoding="utf-8"))["verses"]
    plain = {(int(a["surah"]), int(a["number"])): _clean(a["text"]) for s in imlaei["surahs"] for a in s["ayahs"]}
    verses, names, by_name = {}, {}, {}
    for surah in uthmani["surahs"]:
        number = int(surah["id"])
        name = surah["name"].removeprefix("سورة ").strip()
        names[number] = name
        by_name[normalize(name)] = number
        for ayah in surah["ayahs"]:
            key = (number, int(ayah["number"]))
            text = alignment.get(f"{key[0]}:{key[1]}") or _display(ayah["text"])
            verses[key] = Verse(key[0], key[1], text, plain[key])
    if len(verses) != TOTAL_VERSES:
        raise RuntimeError(f"mushaf has {len(verses)} verses, expected {TOTAL_VERSES}")
    return verses, names, by_name


def verses() -> dict[tuple[int, int], Verse]:
    return _mushaf()[0]


def sura_name(sura: int) -> str:
    return _mushaf()[1][sura]


def _sura_number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    key = normalize(token.removeprefix("سورة").strip())
    by_name = _mushaf()[2]
    return by_name.get(key) or by_name.get(key.removeprefix("ال")) or by_name.get("ال" + key)


def parse_ref(ref: str) -> list[tuple[int, int]]:
    """'52:35', 'الطور:35' or '21:30-31' to [(sura, aya), ...]. Raises ValueError if any verse is missing."""
    match = _REF.match(ref)
    if not match:
        raise ValueError(f"مرجع آية غير صالح: {ref}")
    sura = _sura_number(match["sura"])
    start = int(match["start"])
    end = int(match["end"] or start)
    if sura is None or end < start:
        raise ValueError(f"مرجع آية غير صالح: {ref}")
    keys = [(sura, aya) for aya in range(start, end + 1)]
    missing = [f"{s}:{a}" for s, a in keys if (s, a) not in verses()]
    if missing:
        raise ValueError(f"آية غير موجودة في المصحف: {', '.join(missing)}")
    return keys


def lookup(ref: str) -> list[Verse]:
    return [verses()[key] for key in parse_ref(ref)]


def label(ref: str) -> str:
    """Display reference, e.g. «الطور: 35» or «الأنبياء: 30-31»."""
    keys = parse_ref(ref)
    sura, first = keys[0]
    last = keys[-1][1]
    ayat = str(first) if first == last else f"{first}-{last}"
    return f"{sura_name(sura)}: {ayat}"


# --- G3: a verse quoted with a mistake ---

QUOTE_SIGNALS = ("قال تعالي", "قوله تعالي", "قوله", "يقول الله", "الايه", "الايه تقول", "الله يقول")
_QUOTED = re.compile(r"[«\"“](.+?)[»\"”]")
COMMON_BIGRAM_LIMIT = 50


@dataclass(frozen=True)
class Misquote:
    verse: Verse
    ratio: float


@lru_cache(maxsize=1)
def _bigram_index() -> dict[tuple[str, str], tuple[tuple[int, int], ...]]:
    index: dict[tuple[str, str], set] = {}
    for key, verse in verses().items():
        words = _words(verse.plain)
        for pair in zip(words, words[1:]):
            index.setdefault(pair, set()).add(key)
    return {pair: tuple(keys) for pair, keys in index.items() if len(keys) <= COMMON_BIGRAM_LIMIT}


@lru_cache(maxsize=8192)
def _words(text: str) -> tuple[str, ...]:
    from app.arabic import words
    return tuple(words(text))


def _best_window(span: tuple[str, ...], verse_words: tuple[str, ...]) -> float:
    """Best similarity between the shorter sequence and any window of the longer one (±1 word)."""
    from difflib import SequenceMatcher
    short, long_ = (span, verse_words) if len(span) <= len(verse_words) else (verse_words, span)
    target = " ".join(short)
    best = 0.0
    for size in {len(short) - 1, len(short), len(short) + 1}:
        if size < 1 or size > len(long_):
            continue
        for i in range(len(long_) - size + 1):
            ratio = SequenceMatcher(None, target, " ".join(long_[i:i + size])).ratio()
            best = max(best, ratio)
    return best


def find_misquote(message: str) -> Misquote | None:
    """A span of the message that closely resembles a verse but is not its exact text.

    With a quotation signal (quotes, «قال تعالى», «الآية»...): 4+ words and similarity 0.75-0.97.
    Without one: 6+ words and similarity 0.85-0.97. Above 0.97 the quote is taken as correct.
    """
    quoted = _QUOTED.findall(message)
    words = _words(message)
    normalized = " ".join(words)
    after_signal = None
    for sig in sorted(QUOTE_SIGNALS, key=len, reverse=True):
        at = f" {normalized} ".find(f" {sig} ")
        if at >= 0:
            after_signal = tuple(normalized[at + len(sig):].split())
            break
    signal = bool(quoted) or after_signal is not None
    spans = [_words(q) for q in quoted] or [after_signal or words]
    # Without any quotation signal, only a long, very close span counts: paraphrases of a verse's
    # meaning («الله خلق السماوات والأرض في ستة أيام») must not be flagged.
    min_words, low = (4, 0.75) if signal else (8, 0.90)
    best: Misquote | None = None
    exact = False
    index = _bigram_index()
    for span in spans:
        if len(span) < min_words:
            continue
        hits: dict[tuple[int, int], int] = {}
        for pair in zip(span, span[1:]):
            for key in index.get(pair, ()):
                hits[key] = hits.get(key, 0) + 1
        for key, count in hits.items():
            if count < 2:
                continue
            verse = verses()[key]
            ratio = _best_window(span, _words(verse.plain))
            if ratio > 0.97:
                exact = True
            elif ratio >= low and (best is None or ratio > best.ratio):
                best = Misquote(verse, round(ratio, 3))
    return None if exact else best
