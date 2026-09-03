"""Pydantic-схемы запросов и ответов API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Source(BaseModel):
    """Ссылка на фрагмент документа, использованный в ответе."""

    document: str = Field(..., description="Название документа")
    document_id: str
    locator: str = Field("", description="Статья / пункт / раздел")
    chapter: str = Field("", description="Глава документа")
    page: Optional[int] = Field(None, description="Страница (для PDF)")
    chunk_id: str
    score: Optional[float] = Field(None, description="Косинусное сходство, 0..1")
    excerpt: str = Field("", description="Фрагмент текста")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    top_k: Optional[int] = Field(None, ge=1, le=20)


class AskResponse(BaseModel):
    answer: str
    sources: List[Source]
    disclaimer: str
    grounded: bool = Field(
        True, description="False, если в документах не нашлось релевантных фрагментов"
    )


class DocumentInfo(BaseModel):
    id: str
    title: str
    filename: str
    uploaded_at: str
    chunks: int
    size_bytes: int
    pages: Optional[int] = None
    note: str = ""
    excluded_topics: List[str] = Field(
        default_factory=list, description="Темы, исключённые при индексации"
    )
    dropped_sections: List[str] = Field(
        default_factory=list, description="Заголовки выброшенных разделов"
    )


class UploadResponse(BaseModel):
    document: DocumentInfo
    replaced: bool = False


class StatsResponse(BaseModel):
    documents: int
    chunks: int
    embeddings_provider: str
    model: str
