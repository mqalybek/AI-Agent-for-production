"""Тесты нарезки текста и распознавания статей/пунктов."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingest import Block, chunk_blocks, detect_locator  # noqa: E402

SAMPLE = [
    Block("Статья 12. Основания возникновения права недропользования", page=3),
    Block("1. Право недропользования возникает на основании лицензии.", page=3),
    Block("2. Лицензия выдаётся компетентным органом.", page=3),
    Block("Статья 13. Прекращение права недропользования", page=4),
    Block("1. Право недропользования прекращается по истечении срока.", page=4),
]


def test_detect_locator_variants():
    assert detect_locator("Статья 12. Основания") == "Статья 12"
    assert detect_locator("Глава 3 Общие положения") == "Глава 3"
    assert detect_locator("Раздел II") == "Раздел II"
    assert detect_locator("3.1.2) Требования к отчётности") == "3.1.2"
    assert detect_locator("обычный абзац без номера") is None
    assert detect_locator("") is None


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
