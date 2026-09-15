"""Тесты лексического поиска и слияния выдач."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.retrieval import (  # noqa: E402
    BM25Index,
    reciprocal_rank_fusion,
    tokenize,
)

CORPUS = [
    ("a", "Пластовое давление замеряется в добывающих скважинах один раз в месяц."),
    ("b", "Проект разработки месторождения углеводородов содержит гидродинамическую модель."),
    ("c", "Право недропользования прекращается по истечении срока действия контракта."),
]


def test_tokenizer_normalizes_word_forms():
    """Падежные формы сводятся к одной основе — морфологии в зависимостях нет."""
    assert tokenize("пластовое")[0] == tokenize("пластового")[0]
    assert tokenize("скважин")[0] == tokenize("скважинах")[0]
    assert tokenize("и в на для") == []  # стоп-слова


def test_bm25_finds_term_match():
    index = BM25Index.build(CORPUS)
    assert len(index) == 3
    top = index.search("периодичность замера пластового давления", 2)
    assert top[0][0] == "a"
    assert top[0][1] > 0


def test_bm25_ignores_unknown_terms():
    index = BM25Index.build(CORPUS)
    assert index.search("судостроение", 3) == []
    assert index.search("", 3) == []


def test_rrf_prefers_documents_ranked_by_both_sources():
    fused = reciprocal_rank_fusion([(["a", "b"], 1.0), (["b", "c"], 1.0)])
    assert max(fused, key=fused.get) == "b"


def test_rrf_weights_shift_the_winner():
    """Вес лексического источника перевешивает первое место векторного."""
    lexical, vector = ["a", "b"], ["b", "a"]
    balanced = reciprocal_rank_fusion([(lexical, 1.0), (vector, 1.0)])
    assert balanced["a"] == balanced["b"]
    weighted = reciprocal_rank_fusion([(lexical, 1.0), (vector, 0.6)])
    assert weighted["a"] > weighted["b"]


def test_lite_store_searches_without_chromadb(tmp_path):
    """Лёгкое хранилище ищет по файлу — так работает serverless-версия."""
    import json

    from app.lite_store import LiteStore

    index = tmp_path / "index_lite.json"
    index.write_text(
        json.dumps(
            {
                "version": 1,
                "chunks": [
                    {
                        "id": "c1",
                        "text": "Период разведки составляет не более шести последовательных лет.",
                        "title": "Кодекс РК",
                        "doc_id": "d1",
                        "locator": "Статья 116",
                        "chapter": "Глава 17",
                        "page": -1,
                    },
                    {
                        "id": "c2",
                        "text": "Пробная эксплуатация проводится в исследовательских целях.",
                        "title": "Единые правила",
                        "doc_id": "d2",
                        "locator": "пункт 30",
                        "chapter": "Глава 3",
                        "page": 4,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = LiteStore(index)
    hits = store.search("продолжительность периода разведки", 2)
    assert hits[0]["locator"] == "Статья 116"
    assert hits[0]["score"] == 1.0  # лучший результат нормируется к единице
    assert hits[0]["page"] is None and store.search("пробная эксплуатация", 1)[0]["page"] == 4

    assert store.stats()["chunks"] == 2
    assert store.stats()["embeddings_provider"] == "bm25-only"
    assert {d["title"] for d in store.list_documents()} == {"Кодекс РК", "Единые правила"}


def test_lite_store_reports_missing_index(tmp_path):
    from app.lite_store import LiteStore

    try:
        LiteStore(tmp_path / "нет.json")
    except FileNotFoundError as exc:
        assert "export_lite_index" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка об отсутствующем индексе")
