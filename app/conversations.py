"""Хранилище диалогов и оценок ответов (SQLite).

Ассистент должен помнить разговор: пользователь задаёт вопрос, уточняет
(«а если это сложный проект?»), спорит («ты неправильно понял») — и всё это
имеет смысл только вместе с предыдущими репликами. Заодно здесь же копятся
оценки ответов: по ним видно, где ассистент врёт или промахивается мимо нормы.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from .config import settings

RATING_UP = "up"
RATING_DOWN = "down"
RATINGS = {RATING_UP, RATING_DOWN}

# Сколько предыдущих реплик уходит в модель. Пары «вопрос-ответ» по нормам
# длинные, поэтому окно небольшое: контекст нужен для уточнений, а не для
# пересказа всего разговора.
HISTORY_LIMIT = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    position         INTEGER NOT NULL,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    sources          TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, position);
CREATE TABLE IF NOT EXISTS feedback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id       TEXT NOT NULL,
    conversation_id  TEXT NOT NULL,
    rating           TEXT NOT NULL,
    comment          TEXT NOT NULL DEFAULT '',
    question         TEXT NOT NULL DEFAULT '',
    answer           TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    UNIQUE(message_id)
);
"""

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConversationStore:
    """Диалоги и оценки. Файл базы лежит рядом с векторным индексом."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or Path(settings.chroma_dir).parent / "conversations.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------- диалоги
    def create_conversation(self) -> str:
        conversation_id = uuid.uuid4().hex
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
                (conversation_id, _now(), _now()),
            )
        return conversation_id

    def conversation_exists(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return row is not None

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        sources: Optional[List[dict]] = None,
    ) -> str:
        """Записать реплику и вернуть её идентификатор (по нему ставится оценка)."""
        message_id = uuid.uuid4().hex
        with _lock, self._connect() as conn:
            position = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO messages "
                "(id, conversation_id, position, role, content, sources, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    conversation_id,
                    position,
                    role,
                    content,
                    json.dumps(sources or [], ensure_ascii=False),
                    _now(),
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_now(), conversation_id),
            )
        return message_id

    def history(self, conversation_id: str, limit: int = HISTORY_LIMIT) -> List[dict]:
        """Последние реплики диалога в хронологическом порядке."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content, sources, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY position DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "sources": json.loads(row["sources"]),
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]

    def message(self, message_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, conversation_id, role, content FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        return dict(row) if row else None

    def previous_question(self, message_id: str) -> str:
        """Вопрос, на который отвечала эта реплика — нужен для отчёта по оценкам."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT conversation_id, position FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                return ""
            question = conn.execute(
                "SELECT content FROM messages WHERE conversation_id = ? "
                "AND position < ? AND role = 'user' ORDER BY position DESC LIMIT 1",
                (row["conversation_id"], row["position"]),
            ).fetchone()
        return question["content"] if question else ""

    # ------------------------------------------------------------- оценки
    def save_feedback(
        self, message_id: str, rating: str, comment: str = ""
    ) -> Optional[dict]:
        """Сохранить оценку ответа. Повторная оценка перезаписывает прежнюю."""
        if rating not in RATINGS:
            raise ValueError(f"Оценка должна быть {RATING_UP} или {RATING_DOWN}.")
        message = self.message(message_id)
        if message is None or message["role"] != "assistant":
            return None
        record = {
            "message_id": message_id,
            "conversation_id": message["conversation_id"],
            "rating": rating,
            "comment": comment.strip(),
            "question": self.previous_question(message_id),
            "answer": message["content"],
            "created_at": _now(),
        }
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO feedback "
                "(message_id, conversation_id, rating, comment, question, answer, created_at) "
                "VALUES (:message_id, :conversation_id, :rating, :comment, :question, "
                ":answer, :created_at) "
                "ON CONFLICT(message_id) DO UPDATE SET "
                "rating = excluded.rating, comment = excluded.comment, "
                "created_at = excluded.created_at",
                record,
            )
        return record

    def list_feedback(self, limit: int = 100, rating: Optional[str] = None) -> List[dict]:
        query = (
            "SELECT message_id, conversation_id, rating, comment, question, answer, "
            "created_at FROM feedback"
        )
        params: list = []
        if rating:
            query += " WHERE rating = ?"
            params.append(rating)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def feedback_stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT rating, COUNT(*) AS count FROM feedback GROUP BY rating"
            ).fetchall()
            conversations = conn.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0]
        counts = {row["rating"]: row["count"] for row in rows}
        return {
            "conversations": conversations,
            "liked": counts.get(RATING_UP, 0),
            "disliked": counts.get(RATING_DOWN, 0),
        }


_store: Optional[ConversationStore] = None


def get_conversations() -> ConversationStore:
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store
