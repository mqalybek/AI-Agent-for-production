"""Тесты памяти диалога и оценок ответов."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversations import ConversationStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "conversations.db")


def test_history_keeps_order_and_sources(store):
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "Сроки периода разведки?")
    store.add_message(
        conversation,
        "assistant",
        "Не более шести лет.",
        [{"document": "Кодекс", "locator": "Статья 116"}],
    )
    store.add_message(conversation, "user", "А если сложный проект?")

    history = store.history(conversation)
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[0]["content"] == "Сроки периода разведки?"
    assert history[1]["sources"][0]["locator"] == "Статья 116"


def test_history_window_keeps_latest_messages(store):
    """В модель уходит хвост разговора, а не весь его объём."""
    conversation = store.create_conversation()
    for number in range(1, 9):
        store.add_message(conversation, "user", f"вопрос {number}")

    history = store.history(conversation, limit=3)
    assert [m["content"] for m in history] == ["вопрос 6", "вопрос 7", "вопрос 8"]


def test_unknown_conversation_is_reported(store):
    assert store.conversation_exists("нет-такого") is False


def test_feedback_stores_question_and_answer(store):
    """Оценка сохраняется вместе с вопросом — иначе её не разобрать в админке."""
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "Кто утверждает проект разработки?")
    message_id = store.add_message(conversation, "assistant", "Недропользователь.")

    record = store.save_feedback(message_id, "down", "Не та статья")
    assert record["question"] == "Кто утверждает проект разработки?"
    assert record["answer"] == "Недропользователь."
    assert record["comment"] == "Не та статья"

    assert store.feedback_stats()["disliked"] == 1
    assert [f["rating"] for f in store.list_feedback(rating="down")] == ["down"]


def test_feedback_can_be_changed(store):
    """Повторная оценка заменяет прежнюю, а не плодит дубликаты."""
    conversation = store.create_conversation()
    message_id = store.add_message(conversation, "assistant", "Ответ")

    store.save_feedback(message_id, "down", "мимо")
    store.save_feedback(message_id, "up")

    assert store.feedback_stats() == {"conversations": 1, "liked": 1, "disliked": 0}


def test_feedback_rejects_bad_rating_and_unknown_message(store):
    conversation = store.create_conversation()
    message_id = store.add_message(conversation, "assistant", "Ответ")
    with pytest.raises(ValueError):
        store.save_feedback(message_id, "maybe")
    assert store.save_feedback("нет-такого", "up") is None


def test_feedback_only_for_assistant_messages(store):
    """Оценивают ответ ассистента, а не собственный вопрос."""
    conversation = store.create_conversation()
    user_message = store.add_message(conversation, "user", "Вопрос")
    assert store.save_feedback(user_message, "up") is None
