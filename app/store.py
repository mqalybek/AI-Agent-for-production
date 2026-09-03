"""Хранилище документов: ChromaDB для векторов + JSON-реестр для метаданных."""
from __future__ import annotations

import json
import logging
import math
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from .config import settings
from .embeddings import get_embedding_function
from .ingest import Chunk
from .retrieval import (
    WEIGHT_LEXICAL,
    WEIGHT_VECTOR,
    BM25Index,
    reciprocal_rank_fusion,
)

_lock = threading.Lock()

# Телеметрия отключена настройкой, но клиент chromadb всё равно пишет в лог
# сообщения о неудачной отправке событий — они нам не нужны.
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DocumentStore:
    """Тонкая обёртка над ChromaDB: документ = набор фрагментов с общим doc_id."""

    def __init__(self) -> None:
        self.chroma_dir = Path(settings.chroma_dir)
        self.upload_dir = Path(settings.upload_dir)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.chroma_dir.parent / "registry.json"

        self._client = chromadb.PersistentClient(
            path=str(self.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
        )
        self._embed = get_embedding_function()
        self._collection = self._client.get_or_create_collection(
            name=settings.collection_name,
            embedding_function=self._embed,
            metadata={"hnsw:space": "cosine"},
        )
        self._bm25: Optional[BM25Index] = None

    # ------------------------------------------------------------------ реестр
    def _read_registry(self) -> Dict[str, dict]:
        if not self.registry_path.exists():
            return {}
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_registry(self, registry: Dict[str, dict]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list_documents(self) -> List[dict]:
        registry = self._read_registry()
        return sorted(registry.values(), key=lambda d: d["uploaded_at"], reverse=True)

    def get_document(self, doc_id: str) -> Optional[dict]:
        return self._read_registry().get(doc_id)

    def find_by_title(self, title: str) -> Optional[dict]:
        for doc in self._read_registry().values():
            if doc["title"].casefold() == title.casefold():
                return doc
        return None

    # ------------------------------------------------------------- индексация
    def add_document(
        self,
        *,
        title: str,
        filename: str,
        chunks: List[Chunk],
        size_bytes: int,
        pages: Optional[int] = None,
        note: str = "",
        doc_id: Optional[str] = None,
        excluded_topics: Optional[List[str]] = None,
        dropped_sections: Optional[List[str]] = None,
    ) -> dict:
        if not chunks:
            raise ValueError("Из файла не удалось извлечь текст (возможно, это скан).")

        doc_id = doc_id or uuid.uuid4().hex
        ids, documents, metadatas = [], [], []
        for chunk in chunks:
            chunk_id = f"{doc_id}:{chunk.index}"
            ids.append(chunk_id)
            # Заголовок главы попадает в индексируемый текст: он даёт контекст
            # и векторной модели, и лексическому поиску.
            header = " ".join(part for part in (chunk.chapter, chunk.locator) if part)
            documents.append(f"{header}\n{chunk.text}" if header else chunk.text)
            metadatas.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "filename": filename,
                    "locator": chunk.locator or "",
                    "chapter": chunk.chapter or "",
                    "section": chunk.section or "",
                    "page": chunk.page if chunk.page is not None else -1,
                    "chunk_index": chunk.index,
                }
            )

        with _lock:
            self._collection.delete(where={"doc_id": doc_id})
            for start in range(0, len(ids), 128):
                stop = start + 128
                self._collection.add(
                    ids=ids[start:stop],
                    documents=documents[start:stop],
                    metadatas=metadatas[start:stop],
                )
            registry = self._read_registry()
            record = {
                "id": doc_id,
                "title": title,
                "filename": filename,
                "uploaded_at": _now(),
                "chunks": len(chunks),
                "size_bytes": size_bytes,
                "pages": pages,
                "note": note,
                "excluded_topics": list(excluded_topics or []),
                "dropped_sections": list(dropped_sections or []),
            }
            registry[doc_id] = record
            self._write_registry(registry)
            self._bm25 = None
        return record

    def delete_document(self, doc_id: str) -> bool:
        with _lock:
            registry = self._read_registry()
            record = registry.pop(doc_id, None)
            if record is None:
                return False
            self._collection.delete(where={"doc_id": doc_id})
            self._write_registry(registry)
            self._bm25 = None
        stored = self.upload_dir / f"{doc_id}_{record['filename']}"
        if stored.exists():
            stored.unlink()
        return True

    # ----------------------------------------------------------------- поиск
    def _hit(self, chunk_id: str, text: str, metadata: dict, score: float) -> dict:
        page = metadata.get("page", -1)
        return {
            "chunk_id": chunk_id,
            "text": text,
            "document": metadata.get("title", "Без названия"),
            "document_id": metadata.get("doc_id", ""),
            "locator": metadata.get("locator", ""),
            "chapter": metadata.get("chapter", ""),
            "page": None if page in (None, -1) else int(page),
            "score": round(score, 4),
        }

    def _ensure_bm25(self) -> BM25Index:
        """Лексический индекс строится лениво и живёт в памяти процесса."""
        if self._bm25 is None:
            data = self._collection.get(include=["documents"])
            pairs = list(zip(data.get("ids", []), data.get("documents", [])))
            self._bm25 = BM25Index.build(pairs)
        return self._bm25

    def _fetch(self, chunk_ids: List[str]) -> dict:
        if not chunk_ids:
            return {}
        data = self._collection.get(ids=chunk_ids, include=["documents", "metadatas"])
        return {
            chunk_id: (data["documents"][i], data["metadatas"][i] or {})
            for i, chunk_id in enumerate(data.get("ids", []))
        }

    def _similarity(self, query: str, chunk_ids: List[str]) -> Dict[str, float]:
        """Косинусное сходство для фрагментов, которых не было в векторной выдаче.

        Их нашёл BM25, и без этого шага пользователь видел бы у самой полезной
        цитаты сходство 0.00.
        """
        if not chunk_ids:
            return {}
        data = self._collection.get(ids=chunk_ids, include=["embeddings"])
        vectors = data.get("embeddings")
        if vectors is None or len(vectors) == 0:
            return {}
        query_vector = self._embed([query])[0]
        query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
        scores: Dict[str, float] = {}
        for position, chunk_id in enumerate(data.get("ids", [])):
            vector = vectors[position]
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            dot = sum(a * b for a, b in zip(query_vector, vector))
            scores[chunk_id] = max(0.0, dot / (query_norm * norm))
        return scores

    def search(self, query: str, top_k: int) -> List[dict]:
        """Гибридный поиск: вектора + BM25, слияние по Reciprocal Rank Fusion."""
        total = self._collection.count()
        if total == 0:
            return []

        depth = min(total, max(top_k * 4, 20))
        vector_result = self._collection.query(
            query_texts=[query],
            n_results=depth,
            include=["documents", "metadatas", "distances"],
        )
        vector_ids: List[str] = vector_result.get("ids", [[]])[0]
        # Косинусное сходство пригодится для показа пользователю.
        similarity = {
            chunk_id: max(0.0, 1.0 - float(vector_result["distances"][0][i]))
            for i, chunk_id in enumerate(vector_ids)
        }
        cached = {
            chunk_id: (
                vector_result["documents"][0][i],
                vector_result["metadatas"][0][i] or {},
            )
            for i, chunk_id in enumerate(vector_ids)
        }

        lexical = self._ensure_bm25().search(query, depth)
        lexical_ids = [chunk_id for chunk_id, _ in lexical]

        vector_weight = (
            1.0 if settings.embeddings_provider == "voyage" else WEIGHT_VECTOR
        )
        fused = reciprocal_rank_fusion(
            [(lexical_ids, WEIGHT_LEXICAL), (vector_ids, vector_weight)]
        )
        ranked = sorted(fused.items(), key=lambda item: item[1], reverse=True)[:top_k]

        missing = [chunk_id for chunk_id, _ in ranked if chunk_id not in cached]
        cached.update(self._fetch(missing))
        similarity.update(self._similarity(query, missing))

        hits: List[dict] = []
        for chunk_id, _ in ranked:
            if chunk_id not in cached:
                continue
            text, metadata = cached[chunk_id]
            hits.append(self._hit(chunk_id, text, metadata, similarity.get(chunk_id, 0.0)))
        return hits

    def stats(self) -> dict:
        return {
            "documents": len(self._read_registry()),
            "chunks": self._collection.count(),
            "embeddings_provider": settings.embeddings_provider,
            "model": settings.anthropic_model,
        }


_store: Optional[DocumentStore] = None


def get_store() -> DocumentStore:
    global _store
    if _store is None:
        _store = DocumentStore()
    return _store
