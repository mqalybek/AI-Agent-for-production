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
        self.hits = hits or []

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
