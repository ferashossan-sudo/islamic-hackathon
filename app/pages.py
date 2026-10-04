"""Renders the single page once at startup, with approved texts HTML-escaped into it."""
import re
from html import escape
from pathlib import Path

from app import texts
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


def render_index(s: Settings) -> str:
    fill = {"بريد الفريق": s.team_email}
    labels = texts.pairs("ui_labels")
    footer_parts = texts.text("footer").split(" · ")

    def replace(match: re.Match) -> str:
        kind, key = match.groups()
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
        if kind == "v":
            return escape(s.version)
        if kind == "f":
            return escape(footer_parts[int(key)].replace("{الإصدار}", "").strip())
        raise KeyError(match.group(0))

    return _PLACEHOLDER.sub(replace, TEMPLATE.read_text(encoding="utf-8"))
