"""Выбор хранилища: полное (ChromaDB) или лёгкое (индекс из файла).

Импортировать `app.store` напрямую нельзя там, где нет ChromaDB, — сам импорт
потянет пакет весом в сотни мегабайт. Поэтому остальной код обращается сюда,
а конкретная реализация подключается лениво.
"""
from __future__ import annotations

from typing import Optional

from .config import settings

_store = None


def get_store():
    """Хранилище документов: LiteStore при LITE_INDEX=1, иначе ChromaDB."""
    global _store
    if _store is not None:
        return _store
    if settings.lite_index:
        from .lite_store import LiteStore

        _store = LiteStore()
    else:
        from .store import get_store as get_chroma_store

        _store = get_chroma_store()
    return _store


def reset_store() -> None:
    """Сбросить кеш — нужно тестам и после переиндексации."""
    global _store
    _store = None
