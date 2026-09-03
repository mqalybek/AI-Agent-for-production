"""Формирование ответа: поиск фрагментов + генерация через Anthropic Claude."""
from __future__ import annotations

from typing import List, Tuple

from anthropic import Anthropic

from .config import DISCLAIMER, settings
from .store import get_store

SYSTEM_PROMPT = f"""Ты — справочный ассистент по законодательству Республики Казахстан
о недропользовании в части УГЛЕВОДОРОДОВ (нефть, газ, газовый конденсат).

ЖЁСТКИЕ ПРАВИЛА:
1. Отвечай ИСКЛЮЧИТЕЛЬНО на основании фрагментов документов, переданных в блоке
   <документы>. Никакие сведения из собственной памяти, общей эрудиции или
   предположений использовать нельзя.
2. Если в переданных фрагментах нет ответа — прямо напиши:
   «В загруженных документах нет сведений, позволяющих ответить на этот вопрос.»
   и предложи уточнить формулировку или загрузить нужный нормативный акт.
   Ничего не додумывай и не выводи по аналогии.
3. Предметная область — только углеводороды. Нормы о добыче урана, твёрдых и
   общераспространённых полезных ископаемых, старательстве в базу не
   загружались. Если вопрос о них — скажи, что это вне предметной области
   ассистента, и не пытайся отвечать по аналогии с углеводородными нормами.
4. После каждого утверждения ставь ссылку на источник в квадратных скобках:
   [Название документа, Статья N] или [Название документа, пункт N] — ровно так,
   как указано в атрибутах фрагмента (title и locator). Если известна страница,
   добавляй её: [Название документа, Статья N, с. 12].
5. Цитируй нормы близко к тексту, не пересказывай их вольно. Прямые цитаты
   заключай в кавычки.
6. Не давай индивидуальных юридических рекомендаций, оценок правомерности
   действий и прогнозов исхода споров. Излагай, что написано в документах.
7. Если фрагменты противоречат друг другу — покажи оба и укажи их источники.
8. Отвечай на языке вопроса пользователя. Структурируй ответ: краткий вывод,
   затем детали со ссылками.
9. Последней строкой ответа всегда добавляй ровно такую дисклеймер-строку:
   «{DISCLAIMER}»
"""

NO_CONTEXT_ANSWER = (
    "В загруженных документах нет сведений, позволяющих ответить на этот вопрос.\n\n"
    "Уточните формулировку вопроса или попросите администратора загрузить "
    "соответствующий нормативный акт в базу.\n\n" + DISCLAIMER
)


def build_context(hits: List[dict]) -> str:
    """Собрать блок <документы> для передачи модели."""
    parts: List[str] = []
    for number, hit in enumerate(hits, start=1):
        attrs = [f'title="{hit["document"]}"']
        if hit.get("locator"):
            attrs.append(f'locator="{hit["locator"]}"')
        if hit.get("chapter"):
            attrs.append(f'chapter="{hit["chapter"]}"')
        if hit.get("page"):
            attrs.append(f'page="{hit["page"]}"')
        parts.append(
            f"<фрагмент id=\"{number}\" {' '.join(attrs)}>\n{hit['text']}\n</фрагмент>"
        )
    return "<документы>\n" + "\n\n".join(parts) + "\n</документы>"


def _client() -> Anthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY не задан — генерация ответов недоступна. "
            "Укажите ключ в .env."
        )
    return Anthropic(api_key=settings.anthropic_api_key)


def answer_question(question: str, top_k: int | None = None) -> Tuple[str, List[dict], bool]:
    """Вернуть (ответ, источники, признак наличия контекста)."""
    hits = get_store().search(question, top_k or settings.top_k)
    if not hits:
        return NO_CONTEXT_ANSWER, [], False

    user_message = (
        f"{build_context(hits)}\n\n"
        f"<вопрос>\n{question}\n</вопрос>\n\n"
        "Ответь строго по правилам из системной инструкции."
    )

    response = _client().messages.create(
        model=settings.anthropic_model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    answer = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()

    if DISCLAIMER not in answer:
        answer = f"{answer}\n\n{DISCLAIMER}"
    return answer, hits, True
