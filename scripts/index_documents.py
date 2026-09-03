#!/usr/bin/env python3
"""Индексация нормативных документов из каталога documents/.

Профиль по умолчанию — «только углеводороды»: разделы про уран, твёрдые
полезные ископаемые, старательство и пространство недр в индекс не попадают.

    python3 scripts/index_documents.py --reset
    python3 scripts/index_documents.py --exclude uranium
    python3 scripts/index_documents.py --keep-all
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.ingest import load_and_chunk  # noqa: E402
from app.sections import HYDROCARBONS_ONLY, TOPIC_LABELS, resolve_topics  # noqa: E402
from app.store import get_store  # noqa: E402

# Человекочитаемые названия — именно они попадают в ссылку на источник.
CATALOG = {
    "kodeks-o-nedrah-i-nedropolzovanii.docx": (
        "Кодекс РК «О недрах и недропользовании»",
        "№ 125-VI ЗРК от 27.12.2017, ред. от 02.03.2026",
    ),
    "edinye-pravila-racionalnoe-kompleksnoe-ispolzovanie-nedr.docx": (
        "Единые правила по рациональному и комплексному использованию недр",
        "приказ Министра энергетики РК № 239 от 15.06.2018, ред. от 21.06.2026",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exclude",
        default=",".join(HYDROCARBONS_ONLY),
        help="Темы через запятую, которые не индексируются.",
    )
    parser.add_argument(
        "--keep-all", action="store_true", help="Индексировать документы целиком."
    )
    parser.add_argument(
        "--reset", action="store_true", help="Очистить индекс перед загрузкой."
    )
    parser.add_argument(
        "--documents", default=str(ROOT / "documents"), help="Каталог с файлами."
    )
    args = parser.parse_args()

    topics = [] if args.keep_all else resolve_topics(args.exclude.split(","))

    if args.reset:
        for path in (Path(settings.chroma_dir), Path(settings.chroma_dir).parent / "registry.json"):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        print("Индекс очищен.")

    store = get_store()
    source_dir = Path(args.documents)
    files = sorted(
        p
        for p in source_dir.glob("*")
        if p.suffix.lower() in {".docx", ".pdf", ".txt", ".md"}
        and p.name.lower() != "readme.md"
        and not p.name.startswith("_")
    )
    if not files:
        print(f"В {source_dir} нет документов.", file=sys.stderr)
        return 1

    if topics:
        print("Исключаются темы: " + ", ".join(TOPIC_LABELS.get(t, t) for t in topics))

    total = 0
    for path in files:
        title, note = CATALOG.get(path.name, (path.stem, ""))
        chunks, dropped = load_and_chunk(
            path, settings.chunk_size, settings.chunk_overlap, topics
        )
        existing = store.find_by_title(title)
        record = store.add_document(
            title=title,
            filename=path.name,
            chunks=chunks,
            size_bytes=path.stat().st_size,
            pages=max((c.page for c in chunks if c.page), default=None),
            note=note,
            doc_id=existing["id"] if existing else None,
            excluded_topics=topics,
            dropped_sections=[d.heading for d in dropped],
        )
        stored = store.upload_dir / f"{record['id']}_{path.name}"
        if not stored.exists():
            shutil.copyfile(path, stored)
        total += record["chunks"]
        print(f"\n{title}: {record['chunks']} фрагментов")
        for item in dropped:
            print(f"  выброшено [{item.topic}] {item.heading} — {item.blocks} абзацев")

    print(f"\nГотово. Фрагментов в индексе: {store.stats()['chunks']} (добавлено {total}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
