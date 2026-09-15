"""Тесты HTTP-слоя: доступ в админку и поведение при пустой выдаче поиска."""
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ADMIN_TOKEN"] = "test-token"

from fastapi.testclient import TestClient  # noqa: E402

from app import main, rag  # noqa: E402
from app.config import DISCLAIMER  # noqa: E402


class FakeStore:
    """Заглушка хранилища: без ChromaDB и без обращений к диску."""

    def __init__(self, hits=None):
        self.hits = list(hits or [])

    def search(self, query, top_k):
        return self.hits

    def list_documents(self):
        return []


@pytest.fixture
def client(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(rag, "get_store", lambda: store)
    monkeypatch.setattr(main, "get_store", lambda: store)
    return TestClient(main.app)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["disclaimer"] == DISCLAIMER


def test_admin_requires_token(client):
    assert client.get("/api/admin/stats").status_code == 401
    assert client.get("/api/admin/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_ask_without_documents_does_not_call_model(client):
    response = client.post("/api/ask", json={"question": "Что такое лицензия?"})
    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is False
    assert body["sources"] == []
    assert "нет сведений" in body["answer"]
    assert body["disclaimer"] == DISCLAIMER


def test_unsupported_format_rejected(client):
    response = client.post(
        "/api/admin/documents",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("archive.zip", io.BytesIO(b"PK"), "application/zip")},
    )
    assert response.status_code == 415


@pytest.fixture
def chat_client(monkeypatch, tmp_path):
    """Клиент с подменённым хранилищем диалогов и без обращений к модели."""
    from app import conversations as conversations_module

    store = FakeStore(
        [
            {
                "chunk_id": "c1",
                "text": "Период разведки составляет не более шести лет.",
                "document": "Кодекс РК «О недрах и недропользовании»",
                "document_id": "d1",
                "locator": "Статья 116",
                "chapter": "Глава 17",
                "page": None,
                "score": 0.7,
            }
        ]
    )
    conversation_store = conversations_module.ConversationStore(tmp_path / "chat.db")
    monkeypatch.setattr(rag, "get_store", lambda: store)
    monkeypatch.setattr(main, "get_store", lambda: store)
    monkeypatch.setattr(main, "get_conversations", lambda: conversation_store)

    captured = {}

    def fake_answer(question, top_k=None, history=None):
        captured["question"] = question
        captured["history"] = history or []
        usage = {"model": "claude-opus-5", "input_tokens": 5500, "output_tokens": 1100}
        return f"Ответ на: {question}", store.hits, True, usage

    monkeypatch.setattr(main, "answer_question", fake_answer)
    client = TestClient(main.app)
    client.captured = captured
    client.conversations = conversation_store
    return client


def test_ask_starts_conversation_and_remembers_it(chat_client):
    """Второй вопрос приходит в тот же диалог и видит предыдущие реплики."""
    first = chat_client.post("/api/ask", json={"question": "Сроки периода разведки?"}).json()
    assert first["conversation_id"] and first["message_id"]
    assert chat_client.captured["history"] == []

    second = chat_client.post(
        "/api/ask",
        json={
            "question": "А если это сложный проект?",
            "conversation_id": first["conversation_id"],
        },
    ).json()

    assert second["conversation_id"] == first["conversation_id"]
    history = chat_client.captured["history"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "Сроки периода разведки?"


def test_unknown_conversation_starts_a_new_one(chat_client):
    """Устаревший идентификатор не ломает чат, а начинает новый диалог."""
    response = chat_client.post(
        "/api/ask", json={"question": "Вопрос", "conversation_id": "чужой-id"}
    )
    assert response.status_code == 200
    assert response.json()["conversation_id"] != "чужой-id"


def test_conversation_history_is_readable(chat_client):
    """Историю можно получить обратно — чат восстанавливается после перезагрузки."""
    created = chat_client.post("/api/ask", json={"question": "Сроки разведки?"}).json()
    history = chat_client.get(f"/api/conversations/{created['conversation_id']}").json()

    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert history["messages"][1]["sources"][0]["locator"] == "Статья 116"
    assert chat_client.get("/api/conversations/нет-такого").status_code == 404


def test_feedback_roundtrip(chat_client):
    """Пользователь отмечает ошибку — администратор видит её в панели."""
    answer = chat_client.post("/api/ask", json={"question": "Кто утверждает проект?"}).json()

    saved = chat_client.post(
        "/api/feedback",
        json={"message_id": answer["message_id"], "rating": "down", "comment": "Не та статья"},
    )
    assert saved.status_code == 200
    assert saved.json()["question"] == "Кто утверждает проект?"

    listed = chat_client.get(
        "/api/admin/feedback", headers={"Authorization": "Bearer test-token"}
    ).json()
    assert listed[0]["comment"] == "Не та статья"

    bad = chat_client.post(
        "/api/feedback", json={"message_id": answer["message_id"], "rating": "маybe"}
    )
    assert bad.status_code == 422
    assert chat_client.post(
        "/api/feedback", json={"message_id": "нет", "rating": "up"}
    ).status_code == 404


def test_without_model_user_still_gets_found_norms(chat_client, monkeypatch):
    """Нет ключа — не ошибка, а найденные нормы: поиск работает без модели."""
    def no_model(question, top_k=None, history=None):
        raise RuntimeError("ANTHROPIC_API_KEY не задан")

    monkeypatch.setattr(main, "answer_question", no_model)

    response = chat_client.post(
        "/api/ask", json={"question": "Сроки периода разведки углеводородов?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["search_only"] is True
    assert body["sources"], "нормы найдены, значит должны вернуться пользователю"
    assert "Статья 116" in body["sources"][0]["locator"]
    assert "поиск по документам работает" in body["answer"].lower()
    # История диалога при этом не теряется — ответ записан как обычная реплика.
    history = chat_client.get(f"/api/conversations/{body['conversation_id']}").json()
    assert len(history["messages"]) == 2
