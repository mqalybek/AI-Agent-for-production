"""Лексический поиск BM25 и слияние с векторной выдачей.

Векторный поиск на многоязычной лёгкой модели плохо различает близкие
юридические термины: «пробная эксплуатация» и «опытно-промышленная разработка»
для него почти одно и то же. Лексический BM25, наоборот, точно ловит термин, но
не понимает синонимов. Ранги двух списков объединяются по формуле Reciprocal
Rank Fusion — она не требует калибровки шкал и устойчиво работает «из коробки».
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

TOKEN_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)

# Служебные слова русского юридического текста: в запросе они только шумят.
STOPWORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще",
    "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли",
    "если", "уже", "или", "быть", "был", "него", "до", "вас", "нибудь", "опять",
    "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей", "может", "они",
    "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя", "их", "чем", "была",
    "сам", "чтоб", "без", "будто", "чего", "раз", "тоже", "себе", "под", "будет",
    "ж", "тогда", "кто", "этот", "того", "потому", "этого", "какой", "совсем",
    "ним", "здесь", "этом", "один", "почти", "мой", "тем", "чтобы", "нее",
    "были", "куда", "зачем", "всех", "никогда", "можно", "при", "наконец",
    "два", "об", "другой", "хоть", "после", "над", "больше", "тот", "через",
    "эти", "нас", "про", "всего", "них", "какая", "много", "разве", "три",
    "эту", "моя", "впрочем", "хорошо", "свою", "этой", "перед", "иногда",
    "лучше", "чуть", "том", "нельзя", "такой", "им", "более", "всегда",
    "конечно", "всю", "между", "это", "также", "либо", "иные", "иных",
}

# Длина усечения слова — грубая замена морфологическому анализатору:
# «пластовое», «пластового», «пластовом» сводятся к одной основе.
STEM_LEN = 6

# Константы BM25 из классической реализации Okapi.
BM25_K1 = 1.5
BM25_B = 0.75

# Глубина, на которую RRF смешивает списки (чем больше, тем ровнее вклад).
RRF_K = 60

# Веса источников при слиянии. Для локальной модели эмбеддингов лексический
# поиск точнее; на Voyage разрыв меньше — вес задаётся в настройках.
WEIGHT_LEXICAL = 1.0
WEIGHT_VECTOR = 0.6


def tokenize(text: str) -> List[str]:
    """Слова текста, приведённые к грубой основе."""
    tokens = []
    for match in TOKEN_RE.findall(text.lower()):
        word = match.replace("ё", "е")
        if word in STOPWORDS or len(word) < 3:
            continue
        tokens.append(word[:STEM_LEN])
    return tokens


@dataclass
class BM25Index:
    """Инвертированный индекс по фрагментам. Строится в памяти за один проход."""

    ids: List[str]
    doc_freq: Dict[str, int]
    term_freq: List[Counter]
    lengths: List[int]
    avg_length: float

    @classmethod
    def build(cls, documents: Sequence[Tuple[str, str]]) -> "BM25Index":
        ids: List[str] = []
        term_freq: List[Counter] = []
        lengths: List[int] = []
        doc_freq: Counter = Counter()
        for chunk_id, text in documents:
            tokens = tokenize(text)
            counts = Counter(tokens)
            ids.append(chunk_id)
            term_freq.append(counts)
            lengths.append(len(tokens))
            doc_freq.update(counts.keys())
        avg = sum(lengths) / len(lengths) if lengths else 0.0
        return cls(ids, dict(doc_freq), term_freq, lengths, avg)

    def __len__(self) -> int:
        return len(self.ids)

    def search(self, query: str, limit: int) -> List[Tuple[str, float]]:
        """Идентификаторы фрагментов, отсортированные по релевантности BM25."""
        tokens = tokenize(query)
        if not tokens or not self.ids:
            return []
        total = len(self.ids)
        scores: Dict[int, float] = {}
        for token in set(tokens):
            df = self.doc_freq.get(token, 0)
            if df == 0:
                continue
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            for position, counts in enumerate(self.term_freq):
                freq = counts.get(token)
                if not freq:
                    continue
                norm = 1 - BM25_B + BM25_B * (self.lengths[position] / (self.avg_length or 1))
                contribution = idf * (freq * (BM25_K1 + 1)) / (freq + BM25_K1 * norm)
                scores[position] = scores.get(position, 0.0) + contribution
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
        return [(self.ids[position], score) for position, score in ranked]


def reciprocal_rank_fusion(
    rankings: Iterable[Tuple[Sequence[str], float]], k: int = RRF_K
) -> Dict[str, float]:
    """Слить взвешенные ранжированные списки идентификаторов в один рейтинг.

    Вес нужен потому, что вклад источников неравноценен: на русском
    юридическом тексте лёгкая многоязычная модель эмбеддингов заметно уступает
    лексическому поиску по терминам («пластовое давление», «пробная
    эксплуатация»), и равный вес тянул бы выдачу вниз.
    """
    fused: Dict[str, float] = {}
    for ranking, weight in rankings:
        for position, chunk_id in enumerate(ranking):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + weight / (k + position + 1)
    return fused
