"""Arabic normalization for matching and search. Display text is never normalized."""
import re
import unicodedata

# Harakat, Quranic annotation marks, superscript alef, tatweel, and invisible characters.
_MARKS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u08d3-\u08ff\u0640\u00ad\u200b-\u200f\u2060-\u2064\ufeff]")
_LETTERS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ٲ": "ا", "ٳ": "ا",
    "ى": "ي", "ی": "ي", "ئ": "ي", "ة": "ه", "ؤ": "و", "ء": "", "ک": "ك",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
})
_NON_WORD = re.compile(r"[^\w\s]")
_ARABIC_LETTER = re.compile("[ء-يٱ-ۓ]")
_LETTER = re.compile(r"[^\W\d_]")


def normalize(text: str) -> str:
    """Strip diacritics and tatweel, unify alef/ya/ta marbuta/hamza forms and digits, collapse spaces."""
    text = unicodedata.normalize("NFC", text)
    text = _MARKS.sub("", text).translate(_LETTERS).lower()
    return " ".join(text.split())


def words(text: str) -> list[str]:
    """Normalized words without punctuation."""
    return _NON_WORD.sub(" ", normalize(text)).split()


def arabic_ratio(text: str) -> float:
    """Share of letters that are Arabic; 1.0 for text without letters."""
    letters = _LETTER.findall(text)
    if not letters:
        return 1.0
    return sum(1 for ch in letters if _ARABIC_LETTER.match(ch)) / len(letters)


def trigram_similarity(a: str, b: str) -> float:
    """Jaccard similarity of character trigrams of the normalized texts (same rule as static/app.js)."""
    def grams(text: str) -> set[str]:
        t = " ".join(words(text))
        return {t[i:i + 3] for i in range(len(t) - 2)} if len(t) >= 3 else {t}
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / len(ga | gb) if ga | gb else 0.0
