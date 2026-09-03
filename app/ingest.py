"""Разбор PDF/DOCX и нарезка текста на фрагменты с привязкой к статьям/пунктам."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

# Заголовки нормативных актов: «Статья 12.», «Глава 3», «Раздел II», «Пункт 4»,
# а также нумерация вида «12.3.» в начале абзаца.
LOCATOR_PATTERNS = [
    re.compile(r"^\s*(Статья\s+\d+(?:[-–]\d+)?(?:\.\d+)*)", re.IGNORECASE),
    re.compile(r"^\s*(Глава\s+\d+(?:\.\d+)*)", re.IGNORECASE),
    re.compile(r"^\s*(Раздел\s+[IVXLC\d]+)", re.IGNORECASE),
    re.compile(r"^\s*(Параграф\s+\d+(?:\.\d+)*)", re.IGNORECASE),
    re.compile(r"^\s*(Приложение\s+[\dIVXLC]+)", re.IGNORECASE),
    re.compile(r"^\s*(Пункт\s+\d+(?:\.\d+)*)", re.IGNORECASE),
    re.compile(r"^\s*(\d+(?:\.\d+){1,3})[\.\)]\s+\S"),
]

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


class UnsupportedFormat(ValueError):
    """Формат файла не поддерживается."""


@dataclass
class Block:
    """Абзац исходного документа с известной страницей."""

    text: str
    page: Optional[int] = None


@dataclass
class Chunk:
    """Готовый к индексации фрагмент."""

    text: str
    locator: str = ""
    page: Optional[int] = None
    index: int = 0
    metadata: dict = field(default_factory=dict)


def detect_locator(line: str) -> Optional[str]:
    """Вернуть номер статьи/пункта, если строка выглядит как заголовок."""
    stripped = line.strip()
    if not stripped or len(stripped) > 200:
        return None
    for pattern in LOCATOR_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return match.group(1).strip().rstrip(".")
    return None


def _normalize(text: str) -> str:
    text = text.replace("­", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf(path: Path) -> List[Block]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: List[Block] = []
    for page_no, page in enumerate(reader.pages, start=1):
        text = _normalize(page.extract_text() or "")
        if not text:
            continue
        for paragraph in text.split("\n"):
            if paragraph.strip():
                blocks.append(Block(text=paragraph.strip(), page=page_no))
    return blocks


def extract_docx(path: Path) -> List[Block]:
    import docx  # python-docx

    document = docx.Document(str(path))
    blocks: List[Block] = []
    for paragraph in document.paragraphs:
        text = _normalize(paragraph.text)
        if text:
            blocks.append(Block(text=text))
    for table in document.tables:
        for row in table.rows:
            cells = [_normalize(cell.text) for cell in row.cells]
            line = " | ".join(cell for cell in cells if cell)
            if line:
                blocks.append(Block(text=line))
    return blocks


def extract_plain(path: Path) -> List[Block]:
    text = _normalize(path.read_text(encoding="utf-8", errors="replace"))
    return [Block(text=line.strip()) for line in text.split("\n") if line.strip()]


def extract_blocks(path: Path) -> List[Block]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix in {".txt", ".md"}:
        return extract_plain(path)
    raise UnsupportedFormat(
        f"Формат {suffix or '(без расширения)'} не поддерживается. "
        f"Доступны: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def chunk_blocks(
    blocks: Iterable[Block],
    chunk_size: int = 1200,
    overlap: int = 200,
) -> List[Chunk]:
    """Собрать абзацы во фрагменты ~chunk_size символов.

    Границы статей уважаются: новая «Статья N» всегда начинает новый фрагмент,
    чтобы ссылка на источник оставалась однозначной.
    """
    chunks: List[Chunk] = []
    buffer: List[str] = []
    buffer_len = 0
    current_locator = ""
    chunk_locator = ""
    chunk_page: Optional[int] = None

    def flush() -> None:
        nonlocal buffer, buffer_len, chunk_locator, chunk_page
        text = "\n".join(buffer).strip()
        if text:
            chunks.append(
                Chunk(
                    text=text,
                    locator=chunk_locator,
                    page=chunk_page,
                    index=len(chunks),
                )
            )
        if overlap > 0 and text:
            tail = text[-overlap:]
            buffer = [tail]
            buffer_len = len(tail)
        else:
            buffer = []
            buffer_len = 0

    for block in blocks:
        locator = detect_locator(block.text)
        is_article = bool(locator) and locator.lower().startswith(
            ("статья", "глава", "раздел")
        )
        if locator:
            current_locator = locator
        if is_article and buffer_len > 0:
            flush()
            buffer, buffer_len = [], 0  # без перекрытия через границу статьи
        if not buffer:
            chunk_locator = current_locator
            chunk_page = block.page
        buffer.append(block.text)
        buffer_len += len(block.text) + 1
        if buffer_len >= chunk_size:
            flush()
            chunk_locator = current_locator
            chunk_page = block.page

    if buffer and "".join(buffer).strip():
        text = "\n".join(buffer).strip()
        chunks.append(
            Chunk(text=text, locator=chunk_locator, page=chunk_page, index=len(chunks))
        )

    return [c for c in chunks if len(c.text.strip()) >= 40]


def load_and_chunk(path: Path, chunk_size: int = 1200, overlap: int = 200) -> List[Chunk]:
    return chunk_blocks(extract_blocks(path), chunk_size=chunk_size, overlap=overlap)
