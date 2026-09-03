"""FastAPI-приложение: публичный чат + административная панель."""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from .config import DISCLAIMER, settings
from .ingest import SUPPORTED_SUFFIXES, UnsupportedFormat, load_and_chunk
from .rag import answer_question
from .schemas import (
    AskRequest,
    AskResponse,
    DocumentInfo,
    Source,
    StatsResponse,
    UploadResponse,
)
from .store import get_store

logger = logging.getLogger("subsoil-rag")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="RAG-ассистент по законодательству о недропользовании",
    version="1.0.0",
    description=(
        "Отвечает на вопросы только по загруженным нормативным документам "
        "и указывает источник каждого утверждения."
    ),
)

_bearer = HTTPBearer(auto_error=False)


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
    try:
        answer, hits, grounded = answer_question(question, payload.top_k)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
            page=hit["page"],
            chunk_id=hit["chunk_id"],
            score=hit["score"],
            excerpt=hit["text"][:400] + ("…" if len(hit["text"]) > 400 else ""),
        )
        for hit in hits
    ]
    return AskResponse(
        answer=answer, sources=sources, disclaimer=DISCLAIMER, grounded=grounded
    )


# --------------------------------------------------------------------- админка
@app.get("/api/admin/stats", response_model=StatsResponse, dependencies=[Depends(require_admin)])
def admin_stats() -> StatsResponse:
    return StatsResponse(**get_store().stats())


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
) -> UploadResponse:
    """Загрузить новый документ или обновить существующий (по совпадению названия)."""
    filename = Path(file.filename or "document").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"Формат {suffix or '—'} не поддерживается. "
            f"Доступны: {', '.join(sorted(SUPPORTED_SUFFIXES))}",
        )

    store = get_store()
    doc_title = (title or Path(filename).stem).strip()
    existing = store.find_by_title(doc_title)
    if existing and not replace:
        raise HTTPException(
            status_code=409,
            detail=f"Документ «{doc_title}» уже загружен. "
            "Включите «Обновить существующий», чтобы переиндексировать его.",
        )

    doc_id = existing["id"] if existing else uuid.uuid4().hex
    target = store.upload_dir / f"{doc_id}_{filename}"
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    size_bytes = target.stat().st_size
    limit = settings.max_upload_mb * 1024 * 1024
    if size_bytes > limit:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413, detail=f"Файл больше {settings.max_upload_mb} МБ."
        )

    try:
        chunks = load_and_chunk(target, settings.chunk_size, settings.chunk_overlap)
        pages = max((c.page for c in chunks if c.page), default=None)
        record = store.add_document(
            title=doc_title,
            filename=filename,
            chunks=chunks,
            size_bytes=size_bytes,
            pages=pages,
            note=note.strip(),
            doc_id=doc_id,
        )
    except UnsupportedFormat as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except ValueError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return UploadResponse(document=DocumentInfo(**record), replaced=bool(existing))


@app.delete("/api/admin/documents/{doc_id}", dependencies=[Depends(require_admin)])
def delete_document(doc_id: str) -> dict:
    if not get_store().delete_document(doc_id):
        raise HTTPException(status_code=404, detail="Документ не найден.")
    return {"deleted": doc_id}


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
