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
_TANWEEN = str.maketrans({"ٗ": "ࣰ", "ٞ": "ࣱ", "ٖ": "ࣲ"})
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
    return " ".join(unicodedata.normalize("NFC", text.replace("﻿", "")).split())


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
