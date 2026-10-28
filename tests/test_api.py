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


def test_lite_mode_refuses_document_upload(chat_client, monkeypatch):
    """В serverless-режиме индекс неизменяемый — загрузка должна отказать понятно."""
    import dataclasses
    import io

    from app import config

    monkeypatch.setattr(
        main, "settings", dataclasses.replace(config.settings, lite_index=True)
    )
    response = chat_client.post(
        "/api/admin/documents",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("norma.txt", io.BytesIO("Статья 1. Текст".encode()), "text/plain")},
    )
    assert response.status_code == 503
    assert "облегчённом режиме" in response.json()["detail"]


# --------------------------------------------------------------------------
# Новая редакция документа: настоящее хранилище ChromaDB во временном каталоге


@pytest.fixture
def real_store_client(monkeypatch, tmp_path):
    """Клиент с настоящим хранилищем — замену и дубли нужно проверять на нём."""
    pytest.importorskip("chromadb")
    import dataclasses

    from app import config, store as store_module

    patched = dataclasses.replace(
        config.settings,
        chroma_dir=str(tmp_path / "chroma"),
        upload_dir=str(tmp_path / "uploads"),
        lite_index=False,
    )
    monkeypatch.setattr(store_module, "settings", patched)
    # Векторная модель для проверки логики замены не нужна — подменяем её
    # детерминированной заглушкой, чтобы тест не качал 80 МБ.
    class FakeEmbedding:
        def __call__(self, input):
            return [[float(len(text) % 7), 1.0, 0.5] for text in input]

        def name(self):
            return "fake"

    monkeypatch.setattr(store_module, "get_embedding_function", lambda: FakeEmbedding())
    store = store_module.DocumentStore()
    monkeypatch.setattr(main, "get_store", lambda: store)
    client = TestClient(main.app)
    client.store = store
    return client


ARTICLES = "\n".join(
    f"Статья {n}. Норма номер {n}\n1. Содержание нормы {n} о недропользовании "
    f"углеводородов, достаточно длинное, чтобы стать отдельным фрагментом."
    for n in range(1, 21)
)


def _upload(client, text, **data):
    import io

    return client.post(
        "/api/admin/documents",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("act.txt", io.BytesIO(text.encode("utf-8")), "text/plain")},
        data=data,
    )


def test_new_edition_replaces_old_one(real_store_client):
    """Выбранная редакция заменяется: документ один, старых фрагментов нет."""
    first = _upload(real_store_client, ARTICLES, title="Кодекс о недрах").json()
    doc_id = first["document"]["id"]

    new_text = ARTICLES.replace("Содержание нормы 5", "НОВАЯ РЕДАКЦИЯ нормы 5")
    second = _upload(real_store_client, new_text, replace_id=doc_id, note="ред. 2026")
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["replaced"] is True
    assert body["document"]["id"] == doc_id
    assert body["document"]["title"] == "Кодекс о недрах"  # пустое название не затирает
    assert body["document"]["note"] == "ред. 2026"

    store = real_store_client.store
    assert len(store.list_documents()) == 1
    texts = " ".join(store._collection.get(include=["documents"])["documents"])
    assert "НОВАЯ РЕДАКЦИЯ нормы 5" in texts
    assert "Содержание нормы 5 " not in texts


def test_new_edition_is_recognised_without_any_hints(real_store_client):
    """Просто прикрепили файл: система сама находит, какую редакцию он заменяет."""
    first = _upload(real_store_client, ARTICLES, title="Кодекс о недрах").json()
    doc_id = first["document"]["id"]

    edition = (
        ARTICLES.replace("Содержание нормы 5 ", "Изменённое содержание нормы 5 ")
        .replace(
            "Статья 20. Норма номер 20\n1. Содержание нормы 20 о недропользовании "
            "углеводородов, достаточно длинное, чтобы стать отдельным фрагментом.",
            "",
        )
        + "\nСтатья 21. Новая норма\n1. Недропользователь передаёт данные в цифровой "
        "реестр в электронной форме в установленные сроки."
    )
    response = _upload(real_store_client, edition)  # ни названия, ни выбора
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["auto_detected"] is True
    assert body["replaced"] is True
    assert body["similarity"] > 0.8
    assert body["document"]["id"] == doc_id
    assert body["document"]["title"] == "Кодекс о недрах"
    assert body["document"]["note"].startswith("редакция загружена")
    assert body["changes"]["changed"] == ["Статья 5"]
    assert body["changes"]["added"] == ["Статья 21"]
    assert body["changes"]["removed"] == ["Статья 20"]
    assert len(real_store_client.store.list_documents()) == 1


def test_rollback_restores_previous_edition(real_store_client):
    """Автозамену можно отменить одной кнопкой — и вернуть обратно."""
    doc_id = _upload(real_store_client, ARTICLES, title="Кодекс о недрах").json()["document"]["id"]
    edition = ARTICLES.replace("Содержание нормы 5 ", "НОВОЕ содержание нормы 5 ")
    _upload(real_store_client, edition)

    def texts():
        return " ".join(real_store_client.store._collection.get(include=["documents"])["documents"])

    assert "НОВОЕ содержание нормы 5" in texts()

    headers = {"Authorization": "Bearer test-token"}
    back = real_store_client.post(f"/api/admin/documents/{doc_id}/rollback", headers=headers)
    assert back.status_code == 200, back.text
    assert "НОВОЕ содержание нормы 5" not in texts()
    assert back.json()["changes"]["changed"] == ["Статья 5"]

    # Откат симметричен: текущая редакция стала предыдущей, можно вернуть её.
    again = real_store_client.post(f"/api/admin/documents/{doc_id}/rollback", headers=headers)
    assert again.status_code == 200
    assert "НОВОЕ содержание нормы 5" in texts()


def test_rollback_without_previous_edition_is_404(real_store_client):
    doc_id = _upload(real_store_client, ARTICLES, title="Кодекс").json()["document"]["id"]
    response = real_store_client.post(
        f"/api/admin/documents/{doc_id}/rollback",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 404


def test_new_edition_keeps_excluded_topics(real_store_client):
    """Разделы про уран не возвращаются в базу вместе с новой редакцией."""
    with_uranium = ARTICLES + "\nРАЗДЕЛ VIII. ДОБЫЧА УРАНА\nСтатья 50. Уран\n1. Добыча урана " \
        "методом подземного скважинного выщелачивания проводится по проекту."
    _upload(real_store_client, with_uranium, title="Кодекс", exclude_topics="uranium")
    # Новая редакция приходит с пустым списком тем — как если бы галочки сняли.
    _upload(real_store_client, with_uranium.replace("нормы 3 ", "нормы три "), exclude_topics="")
    texts = " ".join(real_store_client.store._collection.get(include=["documents"])["documents"])
    assert "выщелачивания" not in texts


def test_lite_index_is_refreshed_after_upload(real_store_client):
    """После загрузки индекс для Vercel переписан — остаётся только git push."""
    import gzip
    import json

    _upload(real_store_client, ARTICLES, title="Кодекс")
    path = real_store_client.store.chroma_dir.parent / "index_lite.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    assert {chunk["title"] for chunk in payload["chunks"]} == {"Кодекс"}


def test_different_document_is_accepted(real_store_client):
    """Действительно другой акт проходит без вопросов."""
    _upload(real_store_client, ARTICLES, title="Кодекс о недрах")
    other = "\n".join(
        f"Статья {n}. Правило {n}\n1. Совсем другой текст правил о бурении скважин, "
        f"конструкции и цементаже обсадных колонн, пункт {n}."
        for n in range(1, 21)
    )
    response = _upload(real_store_client, other, title="Правила бурения")
    assert response.status_code == 200, response.text
    assert len(real_store_client.store.list_documents()) == 2


def test_force_new_overrides_similarity_check(real_store_client):
    _upload(real_store_client, ARTICLES, title="Кодекс о недрах")
    response = _upload(
        real_store_client, ARTICLES, title="Кодекс, копия для сравнения", force_new="true"
    )
    assert response.status_code == 200
    assert len(real_store_client.store.list_documents()) == 2


def test_replace_unknown_document_is_404(real_store_client):
    assert _upload(real_store_client, ARTICLES, replace_id="нет-такого").status_code == 404
