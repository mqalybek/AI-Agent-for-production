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
    conversation_id: Optional[str] = Field(
        None, description="Продолжение диалога; пусто — начинается новый"
    )


class AskResponse(BaseModel):
    answer: str
    sources: List[Source]
    disclaimer: str
    grounded: bool = Field(
        True, description="False, если в документах не нашлось релевантных фрагментов"
    )
    conversation_id: str = Field(..., description="Передайте его в следующем вопросе")
    message_id: str = Field(..., description="Идентификатор ответа — по нему ставится оценка")


class HistoryMessage(BaseModel):
    id: str
    role: str
    content: str
    sources: List[Source] = Field(default_factory=list)
    created_at: str


class ConversationResponse(BaseModel):
    conversation_id: str
    messages: List[HistoryMessage]


class FeedbackRequest(BaseModel):
    message_id: str
    rating: str = Field(..., pattern="^(up|down)$", description="up или down")
    comment: str = Field(
        "", max_length=2000, description="Что именно не так — попадёт в админ-панель"
    )


class FeedbackRecord(BaseModel):
    message_id: str
    conversation_id: str
    rating: str
    comment: str = ""
    question: str = ""
    answer: str = ""
    created_at: str


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
    conversations: int = 0
    liked: int = 0
    disliked: int = 0
