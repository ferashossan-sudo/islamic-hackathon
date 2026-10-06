"""Renders the single page once at startup, with approved texts HTML-escaped into it."""
import re
from html import escape
from pathlib import Path

from app import quran, texts
from app.config import Settings

TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"
_PLACEHOLDER = re.compile(r"\{\{(\w+):(\w+)\}\}")
_UNFILLED = re.compile(r"\{[^{}]+\}")


def _lines(key: str, fill: dict[str, str]) -> list[str]:
    out = []
    for line in texts.text(key).splitlines():
        for name, value in fill.items():
            line = line.replace("{" + name + "}", value)
        line = line.strip()
        if line and not _UNFILLED.search(line):  # drop lines whose placeholder has no value yet
            out.append(line)
    return out


def _paragraphs(key: str, fill: dict[str, str]) -> str:
    return "\n".join(f"<p>{escape(line)}</p>" for line in _lines(key, fill))


def _list(key: str, fill: dict[str, str], skip_first: bool = False) -> str:
    lines = _lines(key, fill)[1 if skip_first else 0:]
    return "<ul>\n" + "\n".join(f"<li>{escape(line.removeprefix('- '))}</li>" for line in lines) + "\n</ul>"


def _calls(key: str) -> str:
    """Support numbers as call buttons: «الطوارئ: 911 · المصدر: الدفاع المدني السعودي»."""
    out = []
    for line in _lines(key, {}):
        head, _, source = line.partition(" · ")
        name, _, number = head.partition(":")
        number = number.strip()
        out.append(f'<a class="call" href="tel:{escape(number)}"><span class="call-label">{escape(name.strip())}</span>'
                   f'<bdi class="call-number">{escape(number)}</bdi></a>')
        if source:
            out.append(f'<p class="hint call-source">{escape(source)}</p>')
    return "\n".join(out)


# The splash line, from the mushaf like every verse on the page: Al-Baqarah 260, «قَالَ أَوَلَمۡ تُؤۡمِنۖ … قَلۡبِي».
SPLASH_VERSE = ("2:260", 8, 16)  # words [8, 16) of the verse
_TRAILING_PAUSE = re.compile("[ۖ-ۜ]+$")


def _splash_verse() -> str:
    ref, start, end = SPLASH_VERSE
    words = quran.lookup(ref)[0].text.split()[start:end]
    words[-1] = _TRAILING_PAUSE.sub("", words[-1])
    return " ".join(words)


def render_index(s: Settings) -> str:
    fill = {"بريد الفريق": s.team_email} if s.team_email else {}  # no email set: its line is left out
    labels = texts.pairs("ui_labels")
    footer_parts = texts.text("footer").split(" · ")

    # The welcome and privacy texts name the model provider actually in use.
    claude_in_use = "anthropic" in (s.router_provider, s.converse_provider) and bool(s.anthropic_api_key)

    def replace(match: re.Match) -> str:
        kind, key = match.groups()
        if claude_in_use and key in ("welcome", "privacy_section"):
            key += "_claude"
        if kind == "t":
            return escape(texts.text(key))
        if kind == "l":
            return escape(labels[key])
        if kind == "p":
            return _paragraphs(key, fill)
        if kind == "ul":
            return _list(key, fill)
        if kind == "ulh":  # first line is the heading, rendered by the template
            return _list(key, fill, skip_first=True)
        if kind == "calls":
            return _calls(key)
        if kind == "q":  # the splash verse and its reference
            return escape(_splash_verse() if key == "splash" else quran.label(SPLASH_VERSE[0]))
        if kind == "v":
            return escape(s.version)
        if kind == "f":
            return escape(footer_parts[int(key)].replace("{الإصدار}", "").strip())
        raise KeyError(match.group(0))

    return _PLACEHOLDER.sub(replace, TEMPLATE.read_text(encoding="utf-8"))
