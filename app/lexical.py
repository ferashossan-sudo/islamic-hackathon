"""Local lexical scorer: TF-IDF over character 3-5-grams of normalized words.

The n-grams absorb attached prefixes such as «وال» and «بال». Each phrasing of an entry (its question
and every variant) is indexed separately, and an entry scores as its best-matching phrasing.
It is the gate before the model and the whole retrieval in degraded mode. No embeddings, no external service.
"""
import math
from collections import Counter

from app import arabic

NGRAMS = (3, 4, 5)


def _grams(text: str) -> Counter:
    grams = Counter()
    for word in arabic.words(text):
        padded = f" {word} "
        for n in NGRAMS:
            grams.update(padded[i:i + n] for i in range(len(padded) - n + 1))
    return grams


class LexicalIndex:
    def __init__(self, entries: list[dict]):
        phrasings = [(e["id"], _grams(text)) for e in entries for text in [e["question"], *e.get("variants", [])]]
        df = Counter(gram for _, grams in phrasings for gram in grams)
        n = len(phrasings)
        self.idf = {gram: math.log((1 + n) / (1 + count)) + 1 for gram, count in df.items()}
        self.docs = [(entry_id, self._vector(grams)) for entry_id, grams in phrasings]

    def _vector(self, grams: Counter) -> dict[str, float]:
        weights = {g: (1 + math.log(c)) * self.idf.get(g, 0.0) for g, c in grams.items()}
        norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
        return {g: w / norm for g, w in weights.items() if w}

    def search(self, text: str, k: int = 5) -> list[tuple[str, float]]:
        query = self._vector(_grams(text))
        best: dict[str, float] = {}
        for entry_id, vec in self.docs:
            score = sum(w * vec.get(g, 0.0) for g, w in query.items())
            best[entry_id] = max(best.get(entry_id, 0.0), score)
        ranked = sorted(best.items(), key=lambda pair: (-pair[1], pair[0]))
        return [(entry_id, round(score, 4)) for entry_id, score in ranked[:k] if score > 0]
