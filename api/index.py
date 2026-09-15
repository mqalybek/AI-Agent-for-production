"""Точка входа для Vercel: то же приложение, но в serverless-режиме.

На Vercel нет постоянного диска и нет места под ChromaDB с ONNX-моделью,
поэтому включается лёгкое хранилище: индекс читается из data/index_lite.json,
поиск идёт по BM25. Загрузка новых документов в этом режиме недоступна —
индекс пересобирается локально и попадает на сервер вместе с кодом.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Переменные выставляются до импорта конфигурации: она читается один раз.
os.environ.setdefault("LITE_INDEX", "1")
# Единственный каталог, доступный на запись в serverless-функции.
os.environ.setdefault("CHROMA_DIR", "/tmp/subsoil/chroma")
os.environ.setdefault("UPLOAD_DIR", "/tmp/subsoil/uploads")

from app.main import app  # noqa: E402  импорт после настройки окружения

__all__ = ["app"]
