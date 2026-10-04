"""Approved fixed texts from content/fixed_texts.json. The model never writes these."""
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXED_TEXTS = ROOT / "content" / "fixed_texts.json"


@lru_cache(maxsize=1)
def _all() -> dict[str, str]:
    data = json.loads(FIXED_TEXTS.read_text(encoding="utf-8"))
    return {key: item["text"] for key, item in data.items()}


def text(key: str) -> str:
    return _all()[key]


def pairs(key: str) -> dict[str, str]:
    """Texts written as one `name: value` pair per line (ui_labels, level_labels, ...)."""
    out = {}
    for line in text(key).splitlines():
        name, sep, value = line.partition(":")
        if sep:
            out[name.strip()] = value.strip()
    return out


@lru_cache(maxsize=1)
def _all_en() -> dict[str, str]:
    data = json.loads(FIXED_TEXTS.read_text(encoding="utf-8"))
    return {key: item.get("text_en", "") for key, item in data.items()}


def text_en(key: str) -> str:
    return _all_en()[key]
