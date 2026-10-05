"""Builds answer blocks from an approved entry, the mushaf and fixed texts only, then checks them (step 11).

Nothing here comes from the model. The framing sentence, when present, is added by the pipeline after G5.
"""
import re

from app import kb, quran, texts


def segments(text: str) -> list[dict]:
    """Split text on {{q:s:a}} placeholders into text and verse segments; verse text comes from the mushaf."""
    out, pos = [], 0
    for match in kb.VERSE_PLACEHOLDER.finditer(text or ""):
        if match.start() > pos:
            out.append({"type": "text", "text": text[pos:match.start()]})
        out.append(verse_item(match.group(1)))
        pos = match.end()
    if pos < len(text or ""):
        out.append({"type": "text", "text": text[pos:]})
    return out


CHAT_PLACEHOLDER = re.compile(r"\{\{(q|h):([^}]+)\}\}")


def chat_segments(text: str, entry: dict, quoted: frozenset = frozenset()) -> list[dict]:
    """The model's reply split into text, verse (from the mushaf) and hadith (from the entry) segments.

    `quoted` holds the (kind, value) placeholders already shown earlier in the conversation: they point back.
    """
    out, pos, seen = [], 0, set(quoted)
    hadiths = entry.get("hadiths", [])
    for match in CHAT_PLACEHOLDER.finditer(text):
        if match.start() > pos:
            out.append({"type": "text", "text": text[pos:match.start()]})
        kind, value = match.groups()
        if (kind, value) in seen:  # a second mention points back instead of repeating the whole text
            out.append({"type": "text", "text": f"[{quran.label(value)}]" if kind == "q" else "(الحديث السابق)"})
        elif kind == "q":
            out.append(verse_item(value))
        else:
            h = hadiths[int(value) - 1]
            out.append({"type": "hadith", "text": h["text"], "url": h["url"],
                        "line": _fill(texts.text("hadith_attribution_line"), المصدر=h["source"], الرقم=str(h["number"]),
                                      الدرجة=h["grade"], المحدث=h["grader"])})
        seen.add((kind, value))
        pos = match.end()
    if pos < len(text):
        out.append({"type": "text", "text": text[pos:]})
    return out


def question_text(text: str) -> str:
    """A question as shown on a chip and sent back when tapped: verse placeholders become the mushaf text."""
    return kb.VERSE_PLACEHOLDER.sub(
        lambda m: "﴿" + " ".join(v.plain for v in quran.lookup(m.group(1))) + "﴾", text or "")


def verse_item(ref: str) -> dict:
    return {"type": "verse", "ref": ref, "label": quran.label(ref),
            "text": " ".join(v.text for v in quran.lookup(ref))}


def _fill(template: str, **values: str) -> str:
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template


def answer_blocks(entry: dict, approved: dict[str, dict], layer: str = "summary") -> list[dict]:
    source = entry["source"]
    badge_key = "badge_reviewed_paraphrase" if entry.get("transfer") == "paraphrase" else "badge_reviewed"
    blocks = []
    if entry["level"] == "C":
        blocks.append({"type": "notice", "key": "level_c_notice", "text": texts.text("level_c_notice")})
    blocks.append({
        "type": "answer",
        "badge": _fill(texts.text(badge_key), المصدر=source["name"]),
        "level": texts.pairs("level_labels")[entry["level"]],
        "summary": segments(entry["summary"]),
        "explain_simple": segments(entry["explain_simple"]) if entry.get("explain_simple") else None,
        "body": segments(entry["body"]),
        "open": layer,
    })
    if entry.get("reasoning"):
        blocks.append(reasoning_block(entry["reasoning"]))
    tafsir = [{"mufassir": t["mufassir"], "summary": t["summary"], "url": t["url"],
               "label": _fill(texts.text("tafsir_label"), المفسر=t["mufassir"], المصدر=t["source"])}
              for t in entry.get("tafsir", [])]
    hadiths = [{"text": h["text"], "url": h["url"],
                "line": _fill(texts.text("hadith_attribution_line"), المصدر=h["source"], الرقم=str(h["number"]),
                              الدرجة=h["grade"], المحدث=h["grader"]),
                "via": texts.text("hadith_via_hadeethenc") if h.get("via") == "hadeethenc" else "",
                "verify_url": h.get("verify_url", "")}
               for h in entry.get("hadiths", [])]
    verses = [verse_item(ref) for ref in entry.get("verses", [])]
    if verses or tafsir or hadiths:
        blocks.append({"type": "sharia", "verses": verses, "tafsir": tafsir, "hadiths": hadiths})
    if entry.get("science"):
        labels = texts.pairs("science_degree_labels")
        blocks.append({"type": "science", "note": texts.text("science_block_note"),
                       "items": [{"claim": s["claim"], "degree": s["degree"], "degree_label": labels[s["degree"]],
                                  "source": s["source"], "url": s["url"]} for s in entry["science"]]})
    more = [{"name": m["name"], "locator": m.get("locator", ""), "url": m["url"]} for m in entry.get("more_sources", [])]
    blocks.append({"type": "sources", "items": [{"name": source["name"], "locator": source.get("locator", ""),
                                                 "url": source["url"]}, *more]})
    review_key = "reviewer_attribution_ai_drafted" if entry.get("drafted_with_ai") else "reviewer_attribution"
    blocks.append({"type": "review", "text": _fill(texts.text(review_key), التاريخ=entry["review"]["reviewed_at"])})
    related = [{"id": rid, "question": question_text(approved[rid]["question"])}
               for rid in entry.get("related", []) if rid in approved]
    if related:
        blocks.append({"type": "related", "items": related})
    final_check(blocks, entry)
    return blocks


def reasoning_block(reasoning: dict) -> dict:
    """«بالعقل والعلم»: the reviewed chain of reasoning and the common objections with their answers."""
    labels = texts.pairs("ui_labels")
    return {
        "type": "reasoning",
        "title": labels["reasoning_title"],
        "steps": [{"text": s["text"], "basis_label": labels["basis_" + s["basis"]], "source": s["source"],
                   "locator": s.get("locator", ""), "url": s["url"]} for s in reasoning["steps"]],
        "objections_title": labels["objections_title"],
        "objections": [{"objection": o["objection"], "response": o["response"], "source": o["source"],
                        "locator": o.get("locator", ""), "url": o["url"]} for o in reasoning.get("objections", [])],
    }


def _check_reasoning(block: dict, entry: dict) -> None:
    """The reasoning block shows the entry's reviewed reasoning exactly: same texts, sources and links, nothing more."""
    reasoning = entry.get("reasoning")
    assert reasoning, "reasoning block without reasoning in the entry"
    labels = texts.pairs("ui_labels")
    assert set(block) == {"type", "title", "steps", "objections_title", "objections"}, "reasoning keys"
    assert block["title"] == labels["reasoning_title"] and block["objections_title"] == labels["objections_title"]
    shown = [(s["text"], s["basis_label"], s["source"], s["locator"], s["url"]) for s in block["steps"]]
    stored = [(s["text"], labels["basis_" + s["basis"]], s["source"], s.get("locator", ""), s["url"])
              for s in reasoning["steps"]]
    assert shown == stored and all(len(s) == 5 for s in block["steps"]), "reasoning steps"
    shown = [(o["objection"], o["response"], o["source"], o["locator"], o["url"]) for o in block["objections"]]
    stored = [(o["objection"], o["response"], o["source"], o.get("locator", ""), o["url"])
              for o in reasoning.get("objections", [])]
    assert shown == stored and all(len(o) == 5 for o in block["objections"]), "reasoning objections"


def final_check(blocks: list[dict], entry: dict) -> None:
    """Step 11: every verse equals the mushaf, every hadith and source equals the approved entry.

    Raises AssertionError; the pipeline turns that into the fail-closed card (G12).
    """
    hadith_texts = {h["text"] for h in entry.get("hadiths", [])}
    for block in blocks:
        for seg in block.get("segments", []):
            if seg.get("type") == "hadith":
                assert seg["text"] in hadith_texts, "chat hadith"
        verses = [s for s in block.get("summary", []) + block.get("body", []) + (block.get("explain_simple") or [])
                  + block.get("segments", []) if s.get("type") == "verse"] + block.get("verses", [])
        for verse in verses:
            assert verse["text"] == " ".join(v.text for v in quran.lookup(verse["ref"])), verse["ref"]
        for shown, stored in zip(block.get("hadiths", []), entry.get("hadiths", [])):
            assert shown["text"] == stored["text"] and shown["url"] == stored["url"]
        if block["type"] == "sources" and block.get("items"):
            assert block["items"][0]["url"] == entry["source"]["url"]
            assert [i["url"] for i in block["items"][1:]] in ([], [m["url"] for m in entry.get("more_sources", [])])
        if block["type"] == "sharia":
            assert len(block["hadiths"]) == len(entry.get("hadiths", [])), "hadith count"
        if block["type"] == "reasoning":
            _check_reasoning(block, entry)
