"""G5: the generated framing sentence must carry no claim. Any failed condition drops it silently.

The eight conditions (build-plan §6). The last one is a whitelist: every content word must come from the
user's own message or from the approved neutral connector lexicon, so the model cannot add a ruling,
a source, a fact or a scientific term of its own.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from app import arabic, kb, quran

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


def _core(word: str) -> str:
    """The word without its attached prefixes (فكأنما → كأنما → أنما), so a verse quoted with a different prefix
    («كأنما قتل الناس جميعا» for «فكأنما قتل الناس جميعا») is still recognized."""
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if word.startswith(prefix) and len(word) - len(prefix) >= 3:
                word, changed = word[len(prefix):], True
                break
    return word


def _core_grams(words: list[str]) -> set[tuple[str, ...]]:
    cores = [_core(w) for w in words]
    return {tuple(cores[i:i + 4]) for i in range(len(cores) - 3)}


@lru_cache(maxsize=1)
def _mushaf_core_grams() -> frozenset[tuple[str, ...]]:
    grams = set()
    for verse in quran.verses().values():
        grams |= _core_grams(arabic.words(verse.plain))
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


# --- G13: the conversational reply may use only the approved entry ---

_PLACEHOLDER = re.compile(r"\{\{(q|h):([^}]+)\}\}")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9.\-]*")
REPLY_MIN_CHARS, REPLY_MAX_CHARS = 40, 1400
# Claims the reply may not make unless the approved entry itself makes them.
GUARDED_PHRASES = ("أثبت العلم", "العلم أثبت", "العلم يثبت", "يثبت العلم", "العلم يقول", "العلم يؤكد", "سبق القرآن", "الإعجاز العلمي", "حقيقة علمية", "أجمع العلماء",
                   "اتفق العلماء", "بإجماع", "أجمعوا", "مجمع عليه", "اتفق الفقهاء", "اتفق أهل العلم",
                   "العلماء متفقون", "يقرر العلماء", "يقرر أهل العلم", "الحقيقة أن", "حلال", "حرام", "يجوز",
                   "لا يجوز", "واجب", "فتوى", "كفر", "رواه")
KNOWN_NAMES = ("ابن تيمية", "ابن القيم", "الغزالي", "ابن كثير", "القرطبي", "الطبري", "البغوي", "السعدي", "الشنقيطي",
               "ابن باز", "ابن عثيمين", "الفوزان", "الألباني", "ابن حجر", "النووي", "ابن عباس", "مجاهد", "قتادة",
               "عكرمة", "داروين", "أينشتاين", "هوكينج", "هوكنغ", "نيوتن", "هابل", "لوميتر", "النجار", "زغلول",
               "البخاري", "صحيح مسلم", "الترمذي", "أبو داود", "النسائي", "ابن ماجه", "اللجنة الدائمة")


CLAIM_WORDS = frozenset(arabic.normalize(w) for w in (
    "اتفق", "اتفقت", "اتفقوا", "اتفاق", "إجماع", "أجمع", "أجمعوا", "الجمهور", "جمهور", "جائز", "جواز", "يجوز",
    "يباح", "مباح", "حدا", "عقوبة", "عقوبته", "القتل", "يقتل"))
# Words that point at a named holder in the material («تذكر الموسوعة...»، «في جواب الموقع»); never «العلماء» alone.
HOLDER_WORDS = ("الموسوعة", "الموقع", "الفتوى", "جواب", "اللجنة الدائمة", "الشيخ",
                # unnamed but hedged: the reply does not name its sources, which are listed under it
                "من أهل العلم", "بعض أهل العلم", "بعض العلماء", "من العلماء", "بعض المفسرين", "من المفسرين")
_SENTENCE = re.compile(r"[.!؟?\n]+")


def _unattributed_claim(plain: str, entry: dict, material: str) -> bool:
    material_norm = " " + " ".join(arabic.words(material)) + " "
    holders = [entry["source"]["name"], *[t["mufassir"] for t in entry.get("tafsir", [])],
               *[t["source"] for t in entry.get("tafsir", [])], *HOLDER_WORDS,
               *[n for n in KNOWN_NAMES if f" {' '.join(arabic.words(n))} " in material_norm]]
    holders = [" ".join(arabic.words(h)) for h in holders if h and arabic.words(h)]
    for sentence in _SENTENCE.split(plain):
        words = arabic.words(sentence)
        if not words or not ({w for w in words} | {_core(w) for w in words}) & CLAIM_WORDS:
            continue
        joined = " " + " ".join(words) + " "
        if not any(f" {h} " in joined or (" " + h) in joined for h in holders):
            return True
    return False


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(arabic.normalize(text)))


def check_reply(reply: str, entry: dict, source_names: list[str]) -> bool:
    """G13. False drops the conversational reply; the approved card is then shown alone."""
    return reply_problem(reply, entry, source_names) is None


REASON_ONLY = re.compile(r"بالعقل|بدون دين|بلا دين|بدون (?:آيات|ايات|احاديث|أحاديث|نصوص)|من غير دين|"
                         r"(?:خلني|خلنا|دعني|دعك) من (?:الآيات|الايات|الأحاديث|الاحاديث|النصوص|الدين)|"
                         r"لا تجيب.{0,25}(?:دين|آي|اي|حديث|احاديث|أحاديث)|منطق")
MAX_PLACEHOLDERS, MAX_HADITHS = 2, 1
_RESTATED = re.compile(r"\}\}[\s\)\]»،,:]*(?:أن|إن|أنه|إنه|أنها|إنها|بأن)\s")
# A leading theory or a hypothesis is a witness at its own degree: a sentence that names that degree must not
# also say it negates, proves or settles something («يرجّح أن للكون بداية، وهذا ينفي كونه أزلياً»).
_DEGREE_WORDS = re.compile("نظرية راجحة|فرضية|يرج[ّ]?ح|ترج[ّ]?ح")
_SETTLE_WORDS = re.compile("(?<!لا )(?<!لم )(?:ينفي|تنفي|يثبت|تثبت|يقطع|يحسم|قطع[اً]+|حتم[اً]+|بلا شك|بلا ريب)")


def reply_problem(reply: str, entry: dict, source_names: list[str], message: str = "") -> str | None:
    """The first G13 rule the reply breaks, as a short code for the log (no content), or None."""
    text = (reply or "").strip()
    if not REPLY_MIN_CHARS <= len(text) <= REPLY_MAX_CHARS:
        return "length"
    if "﴾" in text or "﴿" in text:
        return "brackets"
    allowed_verses = {key for ref in entry.get("verses", []) for key in quran.parse_ref(ref)}
    hadith_count = len(entry.get("hadiths", []))
    placeholders = _PLACEHOLDER.findall(text)
    for kind, value in placeholders:
        if kind == "q":
            try:
                if not set(quran.parse_ref(value)) <= allowed_verses:
                    return "verse_placeholder"
            except ValueError:
                return "verse_placeholder"
        if kind == "h" and not (value.isdigit() and 1 <= int(value) <= hadith_count):
            return "hadith_placeholder"
    # Each placeholder expands to a whole verse or hadith: a few keep the reply a conversation, not a wall of text.
    if len(set(placeholders)) > MAX_PLACEHOLDERS or len({v for k, v in placeholders if k == "h"}) > MAX_HADITHS:
        return "placeholders"
    if placeholders and message and REASON_ONLY.search(message):
        return "reason_only"
    if _RESTATED.search(text):  # «{{h:1}} أن الله...» restates the text the placeholder already shows
        return "restated"
    if any(_DEGREE_WORDS.search(s) and _SETTLE_WORDS.search(s) for s in re.split(r"[.؟?!\n؛]", text)):
        return "overclaim"
    plain = _PLACEHOLDER.sub(" ", text)
    # The entry's own prose (without its hadith texts) may share words with a verse; the model may reuse those.
    # It includes the reviewed «بالعقل والعلم» layer: its steps, objections and responses.
    prose = " ".join(str(x) for x in [
        entry.get("question"), entry.get("summary"), entry.get("explain_simple"), entry.get("body"),
        *[s["claim"] for s in entry.get("science", [])],
        *[t["mufassir"] + " " + t["summary"] for t in entry.get("tafsir", [])], entry["source"]["name"],
        *kb.reasoning_texts(entry)])
    material = prose + " " + " ".join(h["text"] for h in entry.get("hadiths", []))
    material_norm = " " + " ".join(arabic.words(material)) + " "
    plain_words = arabic.words(plain)
    plain_norm = " " + " ".join(plain_words) + " "
    # No verse or hadith words written by the model (four consecutive words, compared without prefixes).
    hadith_grams = set()
    for h in entry.get("hadiths", []):
        hadith_grams |= _core_grams(arabic.words(h["text"]))
    prose_grams = _core_grams(arabic.words(prose))
    for gram in _core_grams(plain_words):
        if gram in prose_grams:
            continue  # the reviewed entry itself uses these words in its own text
        if gram in _mushaf_core_grams() or gram in hadith_grams:
            return "verse_or_hadith_words"
    # Level C: a sentence about a ruling, penalty, permission or consensus names who holds it, from the material.
    if entry.get("level") == "C" and _unattributed_claim(plain, entry, material):
        return "attribution"
    # The Prophet ﷺ is mentioned only if the entry mentions him, and his words only through a hadith placeholder.
    prophet = ("رسول الله", "النبي", "ﷺ")
    if any(m in plain for m in prophet) and not (entry.get("hadiths") or any(m in material for m in prophet)):
        return "prophet_mention"
    if ("قال رسول الله" in plain or "قال النبي" in plain) and not any(k == "h" for k, _ in _PLACEHOLDER.findall(text)):
        return "prophet_words"
    # Every number, name, source and guarded claim must already be in the approved entry.
    if not _numbers(plain) <= _numbers(material):
        return "number"
    material_cores = {_core(w) for w in arabic.words(material)}
    plain_cores = {_core(w) for w in plain_words}
    for phrase in GUARDED_PHRASES + KNOWN_NAMES + tuple(source_names):
        words = arabic.words(phrase)
        if not words:
            continue
        if len(words) == 1:  # single words also match with an attached prefix (وحرام، بالإجماع، الإجماع...)
            if _core(words[0]) in plain_cores and _core(words[0]) not in material_cores:
                return "phrase:" + phrase
            continue
        p = " " + " ".join(words) + " "
        if p in plain_norm and p not in material_norm:
            return "phrase:" + phrase
    for word in _LATIN_WORD.findall(plain):
        if word.lower() not in material.lower():
            return "latin"
    return None
