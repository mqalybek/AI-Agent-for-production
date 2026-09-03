"""Иерархия нормативного акта и тематическая фильтрация разделов.

Нормативные акты по недропользованию покрывают разные виды сырья: углеводороды,
уран, твёрдые полезные ископаемые. Для предметного ассистента лишние разделы —
это шум в ретривере: вопрос про пластовое давление может «притянуть» норму про
скважинное подземное выщелачивание урана. Модуль размечает документ по
иерархии (часть → раздел → глава → статья) и позволяет выбросить целые темы
до индексации.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from .ingest import Block

# Уровни заголовков: чем меньше число, тем крупнее единица деления.
LEVEL_PART = 0
LEVEL_SECTION = 1
LEVEL_CHAPTER = 2
LEVEL_ARTICLE = 3

HEADING_RULES: List[Tuple[re.Pattern, int]] = [
    (re.compile(r"^(ОБЩАЯ|ОСОБЕННАЯ)\s+ЧАСТЬ\b", re.IGNORECASE), LEVEL_PART),
    (re.compile(r"^РАЗДЕЛ\s+[IVXLC\d]+", re.IGNORECASE), LEVEL_SECTION),
    (re.compile(r"^ПРИЛОЖЕНИЕ\s*\d*", re.IGNORECASE), LEVEL_SECTION),
    (re.compile(r"^ГЛАВА\s+\d+", re.IGNORECASE), LEVEL_CHAPTER),
    (re.compile(r"^ПАРАГРАФ\s+\d+", re.IGNORECASE), LEVEL_CHAPTER),
    (re.compile(r"^СТАТЬЯ\s+\d+", re.IGNORECASE), LEVEL_ARTICLE),
]

MAX_HEADING_LEN = 250

# Тематические фильтры: имя -> регулярка по тексту заголовка раздела/главы/статьи.
TOPIC_FILTERS: Dict[str, re.Pattern] = {
    "uranium": re.compile(r"\bуран(а|у|ом|е|ов)?\b|урановы", re.IGNORECASE),
    "solid_minerals": re.compile(
        r"тверд(ых|ые)\s+полезных?\s+ископаемых?|общераспространенн", re.IGNORECASE
    ),
    "prospecting": re.compile(r"\bстарательств", re.IGNORECASE),
    "underground_space": re.compile(r"пространств[ао]\s+недр", re.IGNORECASE),
}

TOPIC_LABELS: Dict[str, str] = {
    "uranium": "Добыча урана",
    "solid_minerals": "Твёрдые и общераспространённые полезные ископаемые",
    "prospecting": "Старательство",
    "underground_space": "Использование пространства недр",
}

# Набор для предметного ассистента по углеводородам. Использование пространства
# недр сюда не входит намеренно: подземное хранение газа — часть нефтегазовой
# практики, и эти нормы нужны в индексе.
HYDROCARBONS_ONLY = ("uranium", "solid_minerals", "prospecting")

# Префикс структурной единицы, который не несёт темы: «РАЗДЕЛ X.», «Глава 33.».
_NUMBER_PREFIX = re.compile(
    r"^\s*(?:РАЗДЕЛ|ГЛАВА|ПАРАГРАФ|СТАТЬЯ|ПРИЛОЖЕНИЕ)\s+[IVXLC\d.\-]+\s*[.:]?\s*",
    re.IGNORECASE,
)


@dataclass
class DroppedSection:
    """Что именно выброшено — для отчёта администратору."""

    heading: str
    topic: str
    blocks: int


def classify_heading(text: str) -> int | None:
    """Уровень заголовка или None, если строка — обычный абзац."""
    stripped = text.strip()
    if not stripped or len(stripped) > MAX_HEADING_LEN:
        return None
    for pattern, level in HEADING_RULES:
        if pattern.match(stripped):
            return level
    return None


def is_mixed_heading(text: str, patterns: Sequence[Tuple[str, re.Pattern]]) -> bool:
    """Заголовок перечисляет несколько тем, и не все они под фильтром.

    Пример: «РАЗДЕЛ X. ИСПОЛЬЗОВАНИЕ ПРОСТРАНСТВА НЕДР, СТАРАТЕЛЬСТВО,
    ЗАКЛЮЧИТЕЛЬНЫЕ И ПЕРЕХОДНЫЕ ПОЛОЖЕНИЯ» — выбрасывать его целиком нельзя,
    иначе вместе со старательством пропадут переходные положения, которые
    действуют и для углеводородных контрактов. Такой раздел пропускаем и режем
    на уровне глав.
    """
    title = _NUMBER_PREFIX.sub("", text.strip())
    parts = [part.strip() for part in re.split(r"[,;]", title) if part.strip()]
    if len(parts) < 2:
        return False
    return any(
        not any(pattern.search(part) for _, pattern in patterns) for part in parts
    )


def _short(text: str) -> str:
    return " ".join(text.split())[:180]


def annotate_blocks(blocks: Iterable[Block]) -> List[Block]:
    """Проставить каждому блоку его раздел и главу (in place)."""
    section = ""
    chapter = ""
    result: List[Block] = []
    for block in blocks:
        level = classify_heading(block.text)
        if level == LEVEL_PART:
            section, chapter = "", ""
        elif level == LEVEL_SECTION:
            section, chapter = _short(block.text), ""
        elif level == LEVEL_CHAPTER:
            chapter = _short(block.text)
        block.section = section
        block.chapter = chapter
        result.append(block)
    return result


def resolve_topics(topics: Sequence[str]) -> List[str]:
    """Проверить имена фильтров и вернуть их в каноническом виде."""
    resolved: List[str] = []
    for topic in topics:
        name = topic.strip().lower()
        if not name:
            continue
        if name not in TOPIC_FILTERS:
            raise ValueError(
                f"Неизвестная тема {topic!r}. Доступны: {', '.join(sorted(TOPIC_FILTERS))}"
            )
        resolved.append(name)
    return resolved


def filter_blocks(
    blocks: Iterable[Block], exclude_topics: Sequence[str] = ()
) -> Tuple[List[Block], List[DroppedSection]]:
    """Убрать разделы, главы и статьи, чьи заголовки попадают под фильтры.

    Выбрасывается заголовок и всё его содержимое до следующего заголовка того же
    или более высокого уровня. Абзацы, где тема лишь упомянута (например,
    перечисление видов операций в общей части), сохраняются: они нужны для
    ответов по углеводородам.
    """
    topics = resolve_topics(exclude_topics)
    if not topics:
        return list(blocks), []

    patterns = [(name, TOPIC_FILTERS[name]) for name in topics]
    kept: List[Block] = []
    dropped: List[DroppedSection] = []
    skip_level: int | None = None
    current: DroppedSection | None = None

    for block in blocks:
        level = classify_heading(block.text)

        # Заголовок того же или более высокого уровня закрывает пропуск.
        if skip_level is not None and level is not None and level <= skip_level:
            skip_level = None
            current = None

        if skip_level is None and level is not None and not is_mixed_heading(
            block.text, patterns
        ):
            for name, pattern in patterns:
                if pattern.search(block.text):
                    skip_level = level
                    current = DroppedSection(_short(block.text), name, 0)
                    dropped.append(current)
                    break

        if skip_level is not None:
            if current is not None:
                current.blocks += 1
            continue

        kept.append(block)

    return kept, dropped
