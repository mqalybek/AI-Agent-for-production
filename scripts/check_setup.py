#!/usr/bin/env python3
"""Диагностика установки: что не так с .env, ключом и индексом.

    python scripts/check_setup.py

Скрипт ничего не меняет и не показывает ключ целиком — только то, что нужно,
чтобы понять причину отказа.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, FAIL, WARN = "[ OK ]", "[ НЕТ ]", "[ ! ]"


def main() -> int:
    print("Проверка установки\n" + "=" * 60)
    problems = 0

    env_path = ROOT / ".env"
    if not env_path.exists():
        print(f"{FAIL} Файл .env не найден. Скопируйте .env.example в .env.")
        return 1

    raw = env_path.read_bytes()
    print(f"{OK} Файл .env найден ({len(raw)} байт)")
    if raw.startswith(b"\xef\xbb\xbf"):
        print(f"{WARN} В начале файла метка BOM от блокнота — она больше не мешает,")
        print("      но при проблемах пересохраните файл как UTF-8 без BOM.")

    from app.config import settings  # импорт после проверки файла
    from app.rag import describe_key, inspect_key

    key = settings.anthropic_api_key
    if not key:
        print(f"{FAIL} ANTHROPIC_API_KEY не задан в .env")
        problems += 1
    else:
        print(f"{OK} ANTHROPIC_API_KEY прочитан: {describe_key(key)}")
        problem = inspect_key(key)
        if problem:
            print(f"{FAIL} {problem}")
            problems += 1

    if settings.admin_token:
        print(f"{OK} ADMIN_TOKEN задан — вход в админ-панель: {settings.admin_token[:2]}***")
    else:
        print(f"{FAIL} ADMIN_TOKEN пуст — админ-панель будет отключена")
        problems += 1

    print(f"{OK} Модель для ответов: {settings.anthropic_model}")

    chroma = Path(settings.chroma_dir)
    if chroma.exists():
        print(f"{OK} Индекс документов найден: {chroma}")
    else:
        print(f"{WARN} Индекс ещё не построен — он создастся при первом запуске")

    if key and not problems:
        print("\nПроверяю ключ через Anthropic (стоит доли цента)…")
        from app.rag import check_connection

        result = check_connection()
        mark = OK if result["status"] == "ok" else FAIL
        print(f"{mark} {result['message']}")
        if result["status"] != "ok":
            problems += 1

    print("=" * 60)
    if problems:
        print(f"Найдено проблем: {problems}. Исправьте .env и запустите проверку снова.")
        print("После правки .env сервер нужно перезапустить — файл читается при старте.")
    else:
        print("Всё в порядке. Запускайте start.bat и открывайте http://127.0.0.1:8000/")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
