"""Проверка системного промпта и сборки контекста."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DISCLAIMER  # noqa: E402
from app.rag import NO_CONTEXT_ANSWER, SYSTEM_PROMPT, build_context  # noqa: E402


def test_system_prompt_requires_grounding_and_disclaimer():
    assert "ИСКЛЮЧИТЕЛЬНО на основании фрагментов" in SYSTEM_PROMPT
    assert "не заменяет консультацию" in SYSTEM_PROMPT
    assert DISCLAIMER in SYSTEM_PROMPT
    assert "В загруженных документах нет сведений" in SYSTEM_PROMPT


def test_no_context_answer_is_honest():
    assert "нет сведений" in NO_CONTEXT_ANSWER
    assert DISCLAIMER in NO_CONTEXT_ANSWER


def test_build_context_carries_source_attributes():
    context = build_context(
        [
            {
                "document": "Кодекс о недрах",
                "locator": "Статья 12",
                "page": 3,
                "text": "Право недропользования возникает на основании лицензии.",
            }
        ]
    )
    assert 'title="Кодекс о недрах"' in context
    assert 'locator="Статья 12"' in context
    assert 'page="3"' in context
    assert "<документы>" in context


def test_prompt_fixes_answer_language_and_quote_language():
    """Ответ — на языке вопроса, цитата нормы — на языке документа."""
    assert "на языке вопроса пользователя" in SYSTEM_PROMPT
    assert "ЦИТАТЫ норм всегда приводи на языке оригинала" in SYSTEM_PROMPT


def test_prompt_restricts_domain_to_hydrocarbons():
    """Уран и ТПИ — вне предметной области ассистента."""
    assert "только углеводороды" in SYSTEM_PROMPT
    assert "вне предметной области" in SYSTEM_PROMPT


def test_prompt_handles_follow_up_and_disagreement():
    """Ассистент должен понимать уточнения и разбирать возражения по нормам."""
    assert "Разговор может быть многоходовым" in SYSTEM_PROMPT
    assert "не спорь ради спора и не соглашайся автоматически" in SYSTEM_PROMPT


def test_contextualize_without_history_does_not_call_model(monkeypatch):
    """Первый вопрос самодостаточен — лишний вызов модели не нужен."""
    from app import rag

    def explode():
        raise AssertionError("модель не должна вызываться")

    monkeypatch.setattr(rag, "_client", explode)
    assert rag.contextualize("Сроки периода разведки?", []) == "Сроки периода разведки?"


def test_contextualize_falls_back_when_model_fails(monkeypatch):
    """Если переписать вопрос не удалось, поиск идёт по склейке, а не падает."""
    from app import rag

    def failing_client():
        raise RuntimeError("нет связи")

    monkeypatch.setattr(rag, "_client", failing_client)
    history = [
        {"role": "user", "content": "Сроки периода разведки?"},
        {"role": "assistant", "content": "Не более шести лет."},
    ]
    assert rag.contextualize("А если сложный проект?", history) == (
        "Сроки периода разведки? А если сложный проект?"
    )


def test_check_connection_reports_missing_key(monkeypatch):
    """Без ключа проверка не ходит в сеть, а прямо говорит, чего не хватает."""
    import dataclasses

    from app import rag

    # Settings — frozen dataclass, поэтому подменяем объект целиком.
    monkeypatch.setattr(
        rag, "settings", dataclasses.replace(rag.settings, anthropic_api_key="")
    )
    result = rag.check_connection()
    assert result["status"] == "no_key"
    assert "ANTHROPIC_API_KEY" in result["message"]


def test_check_connection_explains_empty_balance(monkeypatch):
    """Отказ в доступе к модели чаще всего означает пустой счёт — так и пишем."""
    import dataclasses

    import httpx
    from anthropic import PermissionDeniedError

    from app import rag

    class FakeMessages:
        def create(self, **kwargs):
            raise PermissionDeniedError(
                "forbidden",
                response=httpx.Response(
                    403, request=httpx.Request("POST", "https://api.anthropic.com")
                ),
                body=None,
            )

    class FakeClient:
        messages = FakeMessages()

    # Ключ должен выглядеть настоящим, иначе проверка формата отсечёт его
    # раньше, чем дело дойдёт до Anthropic.
    real_looking_key = "sk-ant-api03-" + "x" * 90
    monkeypatch.setattr(
        rag,
        "settings",
        dataclasses.replace(rag.settings, anthropic_api_key=real_looking_key),
    )
    monkeypatch.setattr(rag, "_client", lambda: FakeClient())
    result = rag.check_connection()
    assert result["status"] == "no_access"
    assert "пополните баланс" in result["message"].lower()
