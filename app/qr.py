"""QR codes for the sources in a saved conversation («حفظ المحادثة PDF»), so each source opens from the printed page
with a phone camera. Only addresses that appear in the approved answers are encoded; nothing else is accepted."""
import threading
from functools import lru_cache

import segno


def source_urls(entries: list[dict]) -> frozenset[str]:
    """Every "url" value anywhere in the served entries: the answer's source, tafsir, hadiths, science, further
    sources and reasoning layer."""
    found = set()

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "url" and isinstance(item, str) and item.startswith("https://"):
                    found.add(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(entries)
    return frozenset(found)


def warm(urls: frozenset[str]) -> None:
    """Build every code once in the background after start-up, so no visitor's request waits for it."""
    threading.Thread(target=lambda: [modules(url) for url in sorted(urls)], daemon=True).start()


@lru_cache(maxsize=2048)
def modules(url: str) -> tuple[str, ...]:
    """The QR code's rows, "1" for a dark module. Error correction at least L keeps the longest addresses (321
    characters, PDF pages with #page) at 61 modules, so a 28 mm code still has modules of about 0.4 mm for a phone
    camera; segno raises the level when that costs no extra size."""
    code = segno.make_qr(url, error="l")
    return tuple("".join("1" if cell else "0" for cell in row) for row in code.matrix)
