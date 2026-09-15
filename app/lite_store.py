"""Лёгкое хранилище для serverless: индекс из файла, поиск только BM25.

На Vercel и подобных площадках нет постоянного диска, а ChromaDB вместе с
ONNX-моделью эмбеддингов весит сотни мегабайт и в лимит функции не помещается.
Поэтому индекс готовится заранее (`scripts/export_lite_index.py`) и кладётся
в репозиторий, а поиск идёт по BM25 — он на чистом Python и на русском
юридическом тексте работает не хуже лёгкой векторной модели.

Интерфейс совпадает с `DocumentStore.search()`, поэтому остальной код
не замечает подмены.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Dict, List, Optional

from .retrieval import BM25Index

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Сжатый индекс предпочтительнее: в репозитории и в функции он втрое легче.
DEFAULT_INDEX_PATHS = (
    _DATA_DIR / "index_lite.json.gz",
    _DATA_DIR / "index_lite.json",
)


def _default_index_path() -> Path:
    for candidate in DEFAULT_INDEX_PATHS:
        if candidate.exists():
            return candidate
    return DEFAULT_INDEX_PATHS[0]


def _read_index(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


class LiteStore:
    """Поиск по заранее подготовленному индексу, без внешних зависимостей."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else _default_index_path()
        if not self.path.exists():
            raise FileNotFoundError(
                f"Не найден индекс {self.path}. Соберите его командой "
                "python scripts/export_lite_index.py"
            )
        payload = _read_index(self.path)
        self._chunks: List[dict] = payload["chunks"]
        self._by_id: Dict[str, dict] = {chunk["id"]: chunk for chunk in self._chunks}
        self._bm25 = BM25Index.build([(c["id"], c["text"]) for c in self._chunks])

    # --------------------------------------------------------------- поиск
    def search(self, query: str, top_k: int) -> List[dict]:
        hits = []
        # BM25 возвращает абсолютные веса; нормируем по лучшему результату,
        # чтобы «сходство» в интерфейсе осталось в привычном диапазоне 0..1.
        ranked = self._bm25.search(query, top_k)
        if not ranked:
            return []
        best = ranked[0][1] or 1.0
        for chunk_id, score in ranked:
            chunk = self._by_id[chunk_id]
            page = chunk.get("page", -1)
            hits.append(
                {
                    "chunk_id": chunk_id,
                    "text": chunk["text"],
                    "document": chunk.get("title", "Без названия"),
                    "document_id": chunk.get("doc_id", ""),
                    "locator": chunk.get("locator", ""),
                    "chapter": chunk.get("chapter", ""),
                    "page": None if page in (None, -1) else int(page),
                    "score": round(min(1.0, score / best), 4),
                }
            )
        return hits

    # ------------------------------------------------------- совместимость
    def list_documents(self) -> List[dict]:
        documents: Dict[str, dict] = {}
        for chunk in self._chunks:
            doc_id = chunk.get("doc_id", "")
            record = documents.setdefault(
                doc_id,
                {
                    "id": doc_id,
                    "title": chunk.get("title", ""),
                    "filename": "",
                    "uploaded_at": "",
                    "chunks": 0,
                    "size_bytes": 0,
                    "pages": None,
                    "note": "предустановленный индекс",
                    "excluded_topics": [],
                    "dropped_sections": [],
                },
            )
            record["chunks"] += 1
        return list(documents.values())

    def stats(self) -> dict:
        from .config import settings

        return {
            "documents": len(self.list_documents()),
            "chunks": len(self._chunks),
            "embeddings_provider": "bm25-only",
            "model": settings.anthropic_model,
        }
