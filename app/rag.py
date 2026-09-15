"""Формирование ответа: поиск фрагментов + генерация через Anthropic Claude."""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from anthropic import (
    Anthropic,
    APIConnectionError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)

from .config import DISCLAIMER, settings
from .store import get_store

logger = logging.getLogger("subsoil-rag.rag")

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
7-1. Разговор может быть многоходовым. Учитывай предыдущие реплики: понимай
   уточнения («а если это сложный проект?»), возражения («ты не про то ответил»)
   и просьбы переформулировать. Если пользователь говорит, что ответ неверен,
   не спорь ради спора и не соглашайся автоматически: перечитай переданные
   фрагменты и либо признай ошибку и дай исправленный ответ, либо объясни,
   какой именно нормой подтверждается прежний вывод. НОВЫЕ утверждения о
   содержании законодательства бери только из текущего блока <документы>;
   на нормы, уже процитированные ранее в этом диалоге, ссылаться можно.
8. Отвечай на языке вопроса пользователя: спросили по-русски — отвечай
   по-русски, по-казахски — по-казахски, по-английски — по-английски.
   Но ПРЯМЫЕ ЦИТАТЫ норм всегда приводи на языке оригинала документа, без
   перевода: юридическую силу имеет только официальный текст. Если язык
   ответа отличается от языка документа, дай цитату в оригинале, а рядом —
   пояснение на языке вопроса, помеченное как пояснение, а не как норма.
   Структурируй ответ: краткий вывод, затем детали со ссылками.
9. Последней строкой ответа всегда добавляй ровно такую дисклеймер-строку:
   «{DISCLAIMER}»
"""

NO_CONTEXT_ANSWER = (
    "В загруженных документах нет сведений, позволяющих ответить на этот вопрос.\n\n"
    "Уточните формулировку вопроса или попросите администратора загрузить "
    "соответствующий нормативный акт в базу.\n\n" + DISCLAIMER
)


# Отдельная дешёвая модель для служебной задачи — переписать вопрос так, чтобы
# он был понятен поиску без остального диалога.
CONTEXTUALIZE_MODEL = "claude-haiku-4-5"

CONTEXTUALIZE_PROMPT = """Ты помогаешь поисковой системе по нормативным актам.

Дан фрагмент диалога и последняя реплика пользователя. Перепиши эту реплику в
один самостоятельный вопрос, понятный без диалога: подставь предмет обсуждения
вместо местоимений и сокращений («а если сложный проект?» → «какова
продолжительность периода разведки для сложных проектов?»).

Ответь ТОЛЬКО текстом вопроса, без пояснений и кавычек. Если реплика и так
самодостаточна, верни её без изменений."""


def _format_history(history: List[dict]) -> str:
    lines = []
    for message in history:
        who = "Пользователь" if message["role"] == "user" else "Ассистент"
        lines.append(f"{who}: {message['content'][:600]}")
    return "\n".join(lines)


def contextualize(question: str, history: List[dict]) -> str:
    """Свести вопрос с учётом диалога к самостоятельному — для поиска.

    Ретривер ищет по одному тексту и про диалог ничего не знает: запрос «а если
    сложный проект?» сам по себе не найдёт ничего осмысленного.
    """
    if not history:
        return question
    try:
        response = _client().messages.create(
            model=CONTEXTUALIZE_MODEL,
            max_tokens=300,
            system=CONTEXTUALIZE_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"<диалог>\n{_format_history(history)}\n</диалог>\n\n"
                        f"<реплика>\n{question}\n</реплика>"
                    ),
                }
            ],
        )
        rewritten = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        ).strip()
        return rewritten or question
    except Exception:  # noqa: BLE001
        # Переписывание — вспомогательный шаг: если оно не удалось, ищем по
        # склейке с последним вопросом пользователя, а не падаем.
        logger.warning("Не удалось переписать вопрос с учётом диалога", exc_info=True)
        previous = [m["content"] for m in history if m["role"] == "user"]
        return f"{previous[-1]} {question}" if previous else question


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


class ModelUnavailable(RuntimeError):
    """Обращение к модели не удалось по причине, понятной пользователю."""


def _client() -> Anthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY не задан — генерация ответов недоступна. "
            "Укажите ключ в .env."
        )
    return Anthropic(api_key=settings.anthropic_api_key)


def answer_question(
    question: str,
    top_k: Optional[int] = None,
    history: Optional[List[dict]] = None,
) -> Tuple[str, List[dict], bool]:
    """Вернуть (ответ, источники, признак наличия контекста).

    ``history`` — предыдущие реплики диалога в хронологическом порядке
    (``{"role": "user"|"assistant", "content": ...}``). Они и уточняют поиск,
    и передаются модели, чтобы разговор можно было продолжать.
    """
    history = history or []
    search_query = contextualize(question, history)
    hits = get_store().search(search_query, top_k or settings.top_k)
    if not hits:
        return NO_CONTEXT_ANSWER, [], False

    user_message = (
        f"{build_context(hits)}\n\n"
        f"<вопрос>\n{question}\n</вопрос>\n\n"
        "Ответь строго по правилам из системной инструкции."
    )

    # История уходит в модель как обычные реплики: API не хранит состояние,
    # весь диалог отправляется заново на каждом шаге.
    messages = [
        {"role": message["role"], "content": message["content"]}
        for message in history
        if message["role"] in ("user", "assistant") and message["content"].strip()
    ]
    messages.append({"role": "user", "content": user_message})

    try:
        response = _client().messages.create(
            model=settings.anthropic_model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
    # Типовые отказы Anthropic переводим в понятную пользователю причину:
    # исходный текст ошибки говорит на языке HTTP, а не на языке того, кто
    # просто открыл сайт и задал вопрос.
    except AuthenticationError as exc:
        raise ModelUnavailable(
            "Anthropic отклонил ключ API. Проверьте ANTHROPIC_API_KEY в файле .env — "
            "ключ выдаётся на console.anthropic.com."
        ) from exc
    except PermissionDeniedError as exc:
        raise ModelUnavailable(
            "У ключа нет доступа к модели "
            f"{settings.anthropic_model}. Укажите в .env доступную модель "
            "или пополните баланс на console.anthropic.com."
        ) from exc
    except RateLimitError as exc:
        raise ModelUnavailable(
            "Anthropic ограничил частоту запросов. Подождите минуту и повторите вопрос."
        ) from exc
    except APIConnectionError as exc:
        raise ModelUnavailable(
            "Не удалось связаться с Anthropic. Проверьте интернет-соединение."
        ) from exc
    answer = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()

    if DISCLAIMER not in answer:
        answer = f"{answer}\n\n{DISCLAIMER}"
    return answer, hits, True
