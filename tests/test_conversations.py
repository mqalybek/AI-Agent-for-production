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


def test_usage_is_counted_per_model(store):
    """Расход токенов копится по моделям: цены у них разные."""
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "Вопрос")
    store.add_message(
        conversation,
        "assistant",
        "Ответ",
        usage={"model": "claude-opus-5", "input_tokens": 5500, "output_tokens": 1100},
    )
    store.add_message(
        conversation,
        "assistant",
        "Ответ дешевле",
        usage={"model": "claude-haiku-4-5", "input_tokens": 5500, "output_tokens": 1100},
    )

    stats = store.usage_stats()
    assert stats["answers"] == 2
    assert stats["input_tokens"] == 11000
    # 5.5 цента на Opus 5 против 1.1 цента на Haiku 4.5
    assert round(stats["cost_usd"], 4) == round(0.055 + 0.011, 4)
    assert {row["model"] for row in stats["by_model"]} == {
        "claude-opus-5",
        "claude-haiku-4-5",
    }


def test_usage_ignores_messages_without_model_call(store):
    """Реплики пользователя и ответы без обращения к модели в счёт не идут."""
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "Вопрос")
    store.add_message(conversation, "assistant", "В документах нет сведений.")
    assert store.usage_stats() == {
        "answers": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "by_model": [],
    }


def test_old_database_gets_usage_columns(tmp_path):
    """База, созданная прошлой версией, не ломается — колонки дописываются."""
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE conversations (id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT);
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, conversation_id TEXT, position INTEGER,
                role TEXT, content TEXT, sources TEXT DEFAULT '[]', created_at TEXT);
            INSERT INTO conversations VALUES ('c1', '2026-01-01', '2026-01-01');
            INSERT INTO messages VALUES ('m1', 'c1', 1, 'user', 'старый вопрос', '[]', '2026-01-01');
            """
        )

    store = ConversationStore(path)
    assert store.history("c1")[0]["content"] == "старый вопрос"
    assert store.usage_stats()["answers"] == 0
    store.add_message(
        "c1", "assistant", "новый ответ",
        usage={"model": "claude-sonnet-5", "input_tokens": 100, "output_tokens": 50},
    )
    assert store.usage_stats()["input_tokens"] == 100
