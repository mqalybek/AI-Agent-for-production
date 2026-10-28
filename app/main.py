"""FastAPI-приложение: публичный чат + административная панель."""
from __future__ import annotations

import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from .config import DEFAULT_ADMIN_TOKEN, DISCLAIMER, settings
from .conversations import get_conversations
from .ingest import SUPPORTED_SUFFIXES, UnsupportedFormat, load_and_chunk
from .sections import TOPIC_FILTERS, TOPIC_LABELS, resolve_topics
from .rag import answer_question, check_connection, search_only
from .schemas import (
    AskRequest,
    AskResponse,
    ChangeReport,
    ConversationResponse,
    DocumentInfo,
    FeedbackRecord,
    FeedbackRequest,
    HistoryMessage,
    Source,
    StatsResponse,
    UploadResponse,
)
from .storage import get_store

logger = logging.getLogger("subsoil-rag")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Напомнить про пароль из шаблона: с ним админка защищена только на словах."""
    if settings.admin_token == DEFAULT_ADMIN_TOKEN:
        logger.warning(
            "ADMIN_TOKEN остался шаблонным (%s). Смените его в .env, прежде чем "
            "открывать сайт кому-то ещё.",
            DEFAULT_ADMIN_TOKEN,
        )
    yield


app = FastAPI(
    lifespan=lifespan,
    title="RAG-ассистент по законодательству о недропользовании",
    version="1.0.0",
    description=(
        "Отвечает на вопросы только по загруженным нормативным документам "
        "и указывает источник каждого утверждения."
    ),
)

_bearer = HTTPBearer(auto_error=False)

# С какой доли совпадающего текста файл считается новой редакцией уже
# загруженного документа. Замер на настоящих актах: редакция Кодекса даже с
# перенумерацией статей совпадает со старой на 99%, Кодекс и Единые правила
# между собой — на 2–7%. Порог посередине оставляет большой запас.
SIMILARITY_THRESHOLD = 0.5


def require_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    """Проверка токена администратора (заголовок Authorization: Bearer ...)."""
    if not settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_TOKEN не настроен на сервере — админка отключена.",
        )
    if credentials is None or credentials.credentials != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий токен администратора.",
        )


# ------------------------------------------------------------------ публичное API
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "disclaimer": DISCLAIMER}


@app.get("/api/documents", response_model=List[DocumentInfo])
def public_documents() -> List[DocumentInfo]:
    """Список загруженных документов — пользователь должен видеть базу знаний."""
    return [DocumentInfo(**doc) for doc in get_store().list_documents()]


@app.post("/api/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Вопрос не может быть пустым.")

    conversations = get_conversations()
    conversation_id = payload.conversation_id or ""
    if conversation_id and not conversations.conversation_exists(conversation_id):
        # Диалог мог быть заведён в прошлой жизни базы — начинаем новый, а не
        # роняем запрос: для пользователя это просто чат, который продолжается.
        conversation_id = ""
    if not conversation_id:
        conversation_id = conversations.create_conversation()

    history = conversations.history(conversation_id)
    conversations.add_message(conversation_id, "user", question)

    degraded = False
    try:
        answer, hits, grounded, usage = answer_question(question, payload.top_k, history)
    except RuntimeError as exc:
        # Ключа нет, он отклонён или кончился баланс. Поиск по документам при
        # этом работает и ничего не стоит — отдаём найденные нормы, а не ошибку.
        logger.warning("Модель недоступна, отвечаю только поиском: %s", exc)
        answer, hits = search_only(question, payload.top_k, history)
        grounded, usage, degraded = bool(hits), {}, True
    except Exception as exc:  # noqa: BLE001 — не показываем стек наружу
        logger.exception("Ошибка генерации ответа")
        raise HTTPException(
            status_code=502, detail=f"Ошибка обращения к модели: {exc}"
        ) from exc

    sources = [
        Source(
            document=hit["document"],
            document_id=hit["document_id"],
            locator=hit["locator"],
            chapter=hit.get("chapter", ""),
            page=hit["page"],
            chunk_id=hit["chunk_id"],
            score=hit["score"],
            excerpt=hit["text"][:400] + ("…" if len(hit["text"]) > 400 else ""),
        )
        for hit in hits
    ]
    message_id = conversations.add_message(
        conversation_id,
        "assistant",
        answer,
        [source.model_dump() for source in sources],
        usage=usage,
    )
    return AskResponse(
        answer=answer,
        sources=sources,
        disclaimer=DISCLAIMER,
        grounded=grounded,
        search_only=degraded,
        conversation_id=conversation_id,
        message_id=message_id,
    )


@app.get("/api/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation(conversation_id: str) -> ConversationResponse:
    """История диалога — чтобы чат восстанавливался после перезагрузки страницы."""
    conversations = get_conversations()
    if not conversations.conversation_exists(conversation_id):
        raise HTTPException(status_code=404, detail="Диалог не найден.")
    return ConversationResponse(
        conversation_id=conversation_id,
        messages=[
            HistoryMessage(
                id=message["id"],
                role=message["role"],
                content=message["content"],
                sources=[Source(**source) for source in message["sources"]],
                created_at=message["created_at"],
            )
            for message in conversations.history(conversation_id, limit=100)
        ],
    )


@app.post("/api/feedback", response_model=FeedbackRecord)
def leave_feedback(payload: FeedbackRequest) -> FeedbackRecord:
    """Оценка ответа: пригодился или нет, и что именно не так."""
    try:
        record = get_conversations().save_feedback(
            payload.message_id, payload.rating, payload.comment
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Ответ не найден.")
    return FeedbackRecord(**record)


# --------------------------------------------------------------------- админка
@app.get("/api/admin/stats", response_model=StatsResponse, dependencies=[Depends(require_admin)])
def admin_stats() -> StatsResponse:
    conversations = get_conversations()
    return StatsResponse(
        **get_store().stats(),
        **conversations.feedback_stats(),
        **conversations.usage_stats(),
        admin_token_is_default=settings.admin_token == DEFAULT_ADMIN_TOKEN,
    )


@app.get(
    "/api/admin/documents",
    response_model=List[DocumentInfo],
    dependencies=[Depends(require_admin)],
)
def admin_documents() -> List[DocumentInfo]:
    return [DocumentInfo(**doc) for doc in get_store().list_documents()]


@app.post(
    "/api/admin/documents",
    response_model=UploadResponse,
    dependencies=[Depends(require_admin)],
)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    note: str = Form(""),
    replace: bool = Form(False),
    exclude_topics: str = Form(""),
    replace_id: str = Form(""),
    force_new: bool = Form(False),
) -> UploadResponse:
    """Загрузить документ. Новую редакцию уже загруженного система узнаёт сама.

    Достаточно прикрепить файл: если его текст в основном совпадает с одним из
    загруженных документов (порог ``SIMILARITY_THRESHOLD``), это новая
    редакция — старая версия уходит в архив, на её место встаёт новая, в ответ
    приходит перечень изменённых, добавленных и удалённых статей. Откатить
    замену можно через POST /api/admin/documents/{id}/rollback.

    ``replace_id`` — указать заменяемый документ явно. ``force_new`` — считать
    файл отдельным документом, даже если он похож на загруженный.
    ``exclude_topics`` — темы через запятую (GET /api/admin/topics); у новой
    редакции берутся темы, с которыми индексировалась прежняя.
    """
    if settings.lite_index:
        # В serverless-режиме индекс приезжает вместе с кодом, а диск доступен
        # только на чтение: собрать новый индекс здесь негде.
        raise HTTPException(
            status_code=503,
            detail="Загрузка документов недоступна в облегчённом режиме. "
            "Добавьте документ в локальной версии и запушьте изменения — "
            "индекс для сайта обновится автоматически.",
        )
    try:
        topics = resolve_topics(exclude_topics.split(","))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    filename = Path(file.filename or "document").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"Формат {suffix or '—'} не поддерживается. "
            f"Доступны: {', '.join(sorted(SUPPORTED_SUFFIXES))}",
        )

    store = get_store()
    existing = None
    if replace_id:
        existing = store.get_document(replace_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Заменяемый документ не найден.")
    elif title.strip():
        existing = store.find_by_title(title.strip())
        if existing and not replace and force_new:
            raise HTTPException(
                status_code=409,
                detail=f"Документ «{existing['title']}» уже загружен — "
                "отдельный документ должен называться иначе.",
            )

    # Пока неизвестно, новый это документ или редакция старого, файл лежит под
    # временным именем.
    incoming = store.upload_dir / f"incoming_{uuid.uuid4().hex}_{filename}"
    with incoming.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        size_bytes = incoming.stat().st_size
        if size_bytes > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(
                status_code=413, detail=f"Файл больше {settings.max_upload_mb} МБ."
            )

        chunks, dropped = load_and_chunk(
            incoming, settings.chunk_size, settings.chunk_overlap, topics
        )
        if not chunks:
            raise ValueError("Из файла не удалось извлечь текст (возможно, это скан).")

        auto_detected = False
        similarity = None
        if existing is None and not force_new:
            similar = store.find_similar(chunks)
            if similar and similar["share"] >= SIMILARITY_THRESHOLD:
                existing = similar["document"]
                similarity = similar["share"]
                auto_detected = True

        changes = None
        previous = None
        if existing:
            # Новая редакция индексируется с теми же исключёнными темами, что
            # и прежняя: иначе в базу вернулись бы, например, разделы про уран.
            old_topics = existing.get("excluded_topics") or []
            if not replace_id and sorted(old_topics) != sorted(topics):
                topics = old_topics
                chunks, dropped = load_and_chunk(
                    incoming, settings.chunk_size, settings.chunk_overlap, topics
                )
            changes = store.diff_versions(existing["id"], chunks)
            previous = {
                "title": existing["title"],
                "filename": existing["filename"],
                "note": existing.get("note", ""),
                "uploaded_at": existing.get("uploaded_at", ""),
                "excluded_topics": existing.get("excluded_topics") or [],
                "archive_path": store.archive_file(existing["id"], existing["filename"]),
            }
            doc_id = existing["id"]
            doc_title = title.strip() if replace_id and title.strip() else existing["title"]
            doc_note = note.strip() or f"редакция загружена {_today()}"
        else:
            doc_id = uuid.uuid4().hex
            doc_title = (title or Path(filename).stem).strip()
            doc_note = note.strip()

        target = store.upload_dir / f"{doc_id}_{filename}"
        incoming.replace(target)
        record = store.add_document(
            title=doc_title,
            filename=filename,
            chunks=chunks,
            size_bytes=size_bytes,
            pages=max((c.page for c in chunks if c.page), default=None),
            note=doc_note,
            doc_id=doc_id,
            excluded_topics=topics,
            dropped_sections=[d.heading for d in dropped],
            previous=previous,
        )
    except UnsupportedFormat as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        incoming.unlink(missing_ok=True)

    _refresh_lite_index(store)
    return UploadResponse(
        document=DocumentInfo(**record),
        replaced=bool(existing),
        auto_detected=auto_detected,
        similarity=round(similarity, 3) if similarity is not None else None,
        changes=ChangeReport(**changes) if changes else None,
    )


@app.post(
    "/api/admin/documents/{doc_id}/rollback",
    response_model=UploadResponse,
    dependencies=[Depends(require_admin)],
)
def rollback_document(doc_id: str) -> UploadResponse:
    """Вернуть предыдущую редакцию. Текущая при этом сама становится предыдущей."""
    if settings.lite_index:
        raise HTTPException(status_code=503, detail="Откат недоступен в облегчённом режиме.")
    store = get_store()
    current = store.get_document(doc_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Документ не найден.")
    previous = current.get("previous") or {}
    archived = store.upload_dir / previous["archive_path"] if previous.get("archive_path") else None
    if archived is None or not archived.exists():
        raise HTTPException(status_code=404, detail="Предыдущей редакции нет — откатывать некуда.")

    chunks, dropped = load_and_chunk(
        archived, settings.chunk_size, settings.chunk_overlap, previous.get("excluded_topics") or []
    )
    changes = store.diff_versions(doc_id, chunks)
    swapped = {
        "title": current["title"],
        "filename": current["filename"],
        "note": current.get("note", ""),
        "uploaded_at": current.get("uploaded_at", ""),
        "excluded_topics": current.get("excluded_topics") or [],
        "archive_path": store.archive_file(doc_id, current["filename"]),
    }
    archived.replace(store.upload_dir / f"{doc_id}_{previous['filename']}")
    record = store.add_document(
        title=previous["title"],
        filename=previous["filename"],
        chunks=chunks,
        size_bytes=(store.upload_dir / f"{doc_id}_{previous['filename']}").stat().st_size,
        pages=max((c.page for c in chunks if c.page), default=None),
        note=previous.get("note", ""),
        doc_id=doc_id,
        excluded_topics=previous.get("excluded_topics") or [],
        dropped_sections=[d.heading for d in dropped],
        previous=swapped,
    )
    _refresh_lite_index(store)
    return UploadResponse(
        document=DocumentInfo(**record), replaced=True, changes=ChangeReport(**changes)
    )


def _today() -> str:
    from datetime import date

    return date.today().strftime("%d.%m.%Y")


def _refresh_lite_index(store) -> None:
    """Переписать индекс для serverless-версии: сайту на Vercel хватит git push."""
    try:
        store.export_lite_index()
    except Exception:  # noqa: BLE001 — сбой выгрузки не должен отменять загрузку
        logger.exception("Не удалось обновить индекс для serverless-версии")


@app.delete("/api/admin/documents/{doc_id}", dependencies=[Depends(require_admin)])
def delete_document(doc_id: str) -> dict:
    if settings.lite_index:
        raise HTTPException(
            status_code=503,
            detail="Удаление документов недоступно в облегчённом режиме: "
            "индекс собирается локально и разворачивается вместе с кодом.",
        )
    store = get_store()
    if not store.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="Документ не найден.")
    _refresh_lite_index(store)
    return {"deleted": doc_id}


@app.get("/api/admin/model-status", dependencies=[Depends(require_admin)])
def model_status() -> dict:
    """Живая проверка ключа Anthropic — стоит доли цента."""
    return check_connection()


@app.get("/api/admin/topics", dependencies=[Depends(require_admin)])
def available_topics() -> dict:
    """Темы, которые можно исключить при загрузке документа."""
    return {
        "topics": [
            {"name": name, "label": TOPIC_LABELS.get(name, name)}
            for name in sorted(TOPIC_FILTERS)
        ]
    }


@app.get(
    "/api/admin/feedback",
    response_model=List[FeedbackRecord],
    dependencies=[Depends(require_admin)],
)
def admin_feedback(rating: Optional[str] = None, limit: int = 100) -> List[FeedbackRecord]:
    """Оценки пользователей: по ним видно, где ассистент промахивается."""
    if rating not in (None, "up", "down"):
        raise HTTPException(status_code=422, detail="rating должен быть up или down.")
    return [
        FeedbackRecord(**record)
        for record in get_conversations().list_feedback(limit=limit, rating=rating)
    ]


@app.post("/api/admin/search", dependencies=[Depends(require_admin)])
def debug_search(payload: AskRequest) -> dict:
    """Отладка ретривера: что именно находится по запросу, без вызова модели."""
    hits = get_store().search(payload.question, payload.top_k or settings.top_k)
    return {"hits": hits}


# ------------------------------------------------------------------- статика
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")



