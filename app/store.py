"""Хранилище документов: ChromaDB для векторов + JSON-реестр для метаданных."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import logging

import chromadb
from chromadb.config import Settings as ChromaSettings

from .config import settings
from .embeddings import get_embedding_function
from .ingest import Chunk

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
        self._collection = self._client.get_or_create_collection(
            name=settings.collection_name,
            embedding_function=get_embedding_function(),
            metadata={"hnsw:space": "cosine"},
        )

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
    ) -> dict:
        if not chunks:
            raise ValueError("Из файла не удалось извлечь текст (возможно, это скан).")

        doc_id = doc_id or uuid.uuid4().hex
        ids, documents, metadatas = [], [], []
        for chunk in chunks:
            chunk_id = f"{doc_id}:{chunk.index}"
            ids.append(chunk_id)
            documents.append(chunk.text)
            metadatas.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "filename": filename,
                    "locator": chunk.locator or "",
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
            }
            registry[doc_id] = record
            self._write_registry(registry)
        return record

    def delete_document(self, doc_id: str) -> bool:
        with _lock:
            registry = self._read_registry()
            record = registry.pop(doc_id, None)
            if record is None:
                return False
            self._collection.delete(where={"doc_id": doc_id})
            self._write_registry(registry)
        stored = self.upload_dir / f"{doc_id}_{record['filename']}"
        if stored.exists():
            stored.unlink()
        return True

    # ----------------------------------------------------------------- поиск
    def search(self, query: str, top_k: int) -> List[dict]:
        if self._collection.count() == 0:
            return []
        result = self._collection.query(
            query_texts=[query],
            n_results=min(top_k, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        hits: List[dict] = []
        ids = result.get("ids", [[]])[0]
        for position, chunk_id in enumerate(ids):
            metadata = result["metadatas"][0][position] or {}
            distance = result["distances"][0][position]
            page = metadata.get("page", -1)
            hits.append(
                {
                    "chunk_id": chunk_id,
                    "text": result["documents"][0][position],
                    "document": metadata.get("title", "Без названия"),
                    "document_id": metadata.get("doc_id", ""),
                    "locator": metadata.get("locator", ""),
                    "page": None if page in (None, -1) else int(page),
                    # cosine distance -> сходство
                    "score": round(max(0.0, 1.0 - float(distance)), 4),
                }
            )
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
