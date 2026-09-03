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
