"""Функции эмбеддингов.

Важно: у Anthropic нет собственного эндпоинта эмбеддингов — компания
рекомендует сторонних провайдеров (например, Voyage AI). Поэтому генерация
ответов идёт через Claude, а векторизация — через один из провайдеров ниже:

* ``local``  — модель all-MiniLM-L6-v2, встроенная в chromadb (ONNX, офлайн);
* ``voyage`` — Voyage AI, рекомендованный Anthropic провайдер.
"""
from __future__ import annotations

from typing import List, Sequence

import httpx

from .config import settings

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


class VoyageEmbeddingFunction:
    """Эмбеддинги Voyage AI в интерфейсе, который ожидает ChromaDB."""

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise RuntimeError(
                "EMBEDDINGS_PROVIDER=voyage, но VOYAGE_API_KEY не задан."
            )
        self._api_key = api_key
        self._model = model

    def name(self) -> str:  # требуется chromadb >= 0.6
        return f"voyage:{self._model}"

    def __call__(self, input: Sequence[str]) -> List[List[float]]:  # noqa: A002
        texts = list(input)
        if not texts:
            return []
        vectors: List[List[float]] = []
        with httpx.Client(timeout=60) as client:
            for start in range(0, len(texts), 96):
                batch = texts[start : start + 96]
                response = client.post(
                    VOYAGE_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"input": batch, "model": self._model},
                )
                response.raise_for_status()
                payload = response.json()
                ordered = sorted(payload["data"], key=lambda item: item["index"])
                vectors.extend(item["embedding"] for item in ordered)
        return vectors


def get_embedding_function():
    """Вернуть embedding function для коллекции ChromaDB."""
    provider = settings.embeddings_provider
    if provider == "voyage":
        return VoyageEmbeddingFunction(settings.voyage_api_key, settings.voyage_model)
    if provider in {"local", "default", ""}:
        from chromadb.utils import embedding_functions

        return embedding_functions.DefaultEmbeddingFunction()
    raise RuntimeError(
        f"Неизвестный EMBEDDINGS_PROVIDER={provider!r}. Допустимо: local, voyage."
    )
