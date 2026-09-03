"""Тесты нарезки текста и распознавания статей/пунктов."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingest import (  # noqa: E402
    NUMBERING_POINTS,
    Block,
    chunk_blocks,
    detect_locator,
    detect_numbering,
)
from app.sections import annotate_blocks, filter_blocks  # noqa: E402

SAMPLE = [
    Block("Статья 12. Основания возникновения права недропользования", page=3),
    Block("1. Право недропользования возникает на основании лицензии.", page=3),
    Block("2. Лицензия выдаётся компетентным органом.", page=3),
    Block("Статья 13. Прекращение права недропользования", page=4),
    Block("1. Право недропользования прекращается по истечении срока.", page=4),
]


def test_detect_locator_variants():
    assert detect_locator("Статья 12. Основания") == "Статья 12"
    assert detect_locator("3.1.2) Требования к отчётности") == "3.1.2"
    assert detect_locator("обычный абзац без номера") is None
    assert detect_locator("") is None


def test_chapter_is_not_a_locator():
    """Глава и раздел — контекст фрагмента, а не ссылка на норму."""
    assert detect_locator("Глава 3. Общие положения") is None
    assert detect_locator("РАЗДЕЛ II. ПОЛЬЗОВАНИЕ НЕДРАМИ") is None


def test_points_numbering_for_bylaws():
    """В правилах и приказах ссылка на норму — сплошной номер пункта."""
    blocks = [Block("Глава 10. Проект разработки"), Block("100. В зависимости от фазового состояния...")]
    assert detect_numbering(blocks) == NUMBERING_POINTS
    assert detect_locator("100. В зависимости от...", NUMBERING_POINTS) == "пункт 100"
    assert detect_locator("100. В зависимости от...") is None


def test_article_starts_new_chunk():
    chunks = chunk_blocks(SAMPLE, chunk_size=10_000, overlap=0)
    assert len(chunks) == 2
    assert chunks[0].locator == "Статья 12"
    assert chunks[1].locator == "Статья 13"
    assert chunks[0].page == 3
    assert chunks[1].page == 4
    assert "Статья 13" not in chunks[0].text


def test_long_text_is_split_by_size():
    blocks = [Block("Статья 1. Термины", page=1)]
    blocks += [Block(f"Пункт {i}. " + "текст нормы " * 20, page=1) for i in range(1, 15)]
    chunks = chunk_blocks(blocks, chunk_size=600, overlap=50)
    assert len(chunks) > 1
    assert all(c.locator for c in chunks)
    assert all(len(c.text) < 1500 for c in chunks)


def test_short_fragments_are_dropped():
    chunks = chunk_blocks([Block("Стр. 1"), Block("— 2 —")], chunk_size=500, overlap=0)
    assert chunks == []


def test_headings_do_not_become_chunks():
    """Заголовок главы закрывает фрагмент, но сам в индекс не попадает."""
    blocks = annotate_blocks([
        Block("Глава 19. ПРОЕКТНЫЕ ДОКУМЕНТЫ"),
        Block("Статья 135. Проектные документы"),
        Block("1. Разведка и добыча углеводородов ведутся по проектным документам, " * 3),
        Block("Глава 20. ОТДЕЛЬНЫЕ ВОПРОСЫ"),
    ])
    chunks = chunk_blocks(blocks, chunk_size=10_000, overlap=0)
    assert len(chunks) == 1
    assert chunks[0].locator == "Статья 135"
    assert chunks[0].chapter == "Глава 19. ПРОЕКТНЫЕ ДОКУМЕНТЫ"
    assert "Глава 20" not in chunks[0].text


def test_footnotes_are_skipped():
    """«Сноска. Статья 38 с изменениями...» — история правок, а не норма."""
    blocks = [
        Block("Статья 38. Контракт"),
        Block("1. Контракт заключается компетентным органом на срок, установленный настоящим Кодексом."),
        Block("Сноска. Статья 38 с изменениями, внесенными Законом РК от 29.12.2022 № 174-VII."),
    ]
    chunks = chunk_blocks(blocks, chunk_size=10_000, overlap=0)
    assert len(chunks) == 1
    assert "Сноска" not in chunks[0].text


def test_uranium_sections_are_excluded():
    """Раздел про уран выбрасывается целиком, углеводородный остаётся."""
    blocks = annotate_blocks([
        Block("РАЗДЕЛ VII. РАЗВЕДКА И ДОБЫЧА УГЛЕВОДОРОДОВ"),
        Block("Статья 119. Периоды разведки"),
        Block("1. Период разведки составляет шесть лет с даты заключения контракта."),
        Block("РАЗДЕЛ VIII. ДОБЫЧА УРАНА"),
        Block("Статья 173. Особенности добычи урана"),
        Block("1. Добыча урана осуществляется методом подземного скважинного выщелачивания."),
        Block("РАЗДЕЛ IX. РАЗВЕДКА И ДОБЫЧА ТВЕРДЫХ ПОЛЕЗНЫХ ИСКОПАЕМЫХ"),
        Block("Статья 200. Разведка твердых полезных ископаемых"),
        Block("1. Разведка твердых полезных ископаемых проводится на основании лицензии."),
    ])
    kept, dropped = filter_blocks(blocks, ["uranium"])
    text = " ".join(b.text for b in kept)
    assert "выщелачивания" not in text
    assert "Период разведки составляет шесть лет" in text
    assert "твердых полезных ископаемых проводится" in text  # тема не исключалась
    assert [d.topic for d in dropped] == ["uranium"]
    assert dropped[0].blocks == 3


def test_mixed_heading_keeps_transitional_provisions():
    """Раздел с несколькими темами не выбрасывается целиком — режем по главам."""
    blocks = annotate_blocks([
        Block("РАЗДЕЛ X. ИСПОЛЬЗОВАНИЕ ПРОСТРАНСТВА НЕДР, СТАРАТЕЛЬСТВО, ЗАКЛЮЧИТЕЛЬНЫЕ И ПЕРЕХОДНЫЕ ПОЛОЖЕНИЯ"),
        Block("Глава 34. Старательство"),
        Block("1. Старательство осуществляется на основании лицензии на старательство."),
        Block("Глава 35. Заключительные и переходные положения"),
        Block("1. Контракты, заключённые до введения в действие настоящего Кодекса, сохраняют силу."),
    ])
    kept, dropped = filter_blocks(blocks, ["prospecting"])
    text = " ".join(b.text for b in kept)
    assert "сохраняют силу" in text
    assert "лицензии на старательство" not in text
    assert dropped[0].heading.startswith("Глава 34")
