#!/usr/bin/env python3
"""Выгрузка индекса в файл для serverless-развёртывания.

    python scripts/export_lite_index.py

Читает построенный ChromaDB-индекс и сохраняет фрагменты с метаданными в
data/index_lite.json — его использует LiteStore там, где нет постоянного диска.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.store import get_store  # noqa: E402


def main() -> int:
    store = get_store()
    data = store._collection.get(include=["documents", "metadatas"])
    if not data["ids"]:
        print("Индекс пуст — сначала выполните scripts/index_documents.py", file=sys.stderr)
        return 1

    chunks = []
    for position, chunk_id in enumerate(data["ids"]):
        metadata = data["metadatas"][position] or {}
        chunks.append(
            {
                "id": chunk_id,
                "text": data["documents"][position],
                "title": metadata.get("title", ""),
                "doc_id": metadata.get("doc_id", ""),
                "locator": metadata.get("locator", ""),
                "chapter": metadata.get("chapter", ""),
                "page": metadata.get("page", -1),
            }
        )
    chunks.sort(key=lambda chunk: (chunk["title"], chunk["id"]))

    target = ROOT / "data" / "index_lite.json.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": 1, "chunks": chunks}, ensure_ascii=False)
    with gzip.open(target, "wt", encoding="utf-8") as handle:
        handle.write(payload)
    print(f"Сохранено фрагментов: {len(chunks)} → {target}")
    print(f"Размер: {target.stat().st_size / 1024 / 1024:.2f} МБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
