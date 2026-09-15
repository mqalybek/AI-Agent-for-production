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
from .storage import get_store

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
            "Укажите ключ в .env и перезапустите сервер: файл читается при старте."
        )
    return Anthropic(api_key=settings.anthropic_api_key)


SEARCH_ONLY_HEADER = (
    "Связный ответ не сформирован: языковая модель не подключена. "
    "Но поиск по документам работает — вот нормы, которые относятся к вашему "
    "вопросу (полные цитаты в блоке «Источники» ниже):"
)

SEARCH_ONLY_FOOTER = (
    "Чтобы получать готовые ответы со ссылками, укажите ANTHROPIC_API_KEY "
    "в файле .env и перезапустите сервер."
)


def search_only(question: str, top_k: Optional[int] = None,
                history: Optional[List[dict]] = None) -> Tuple[str, List[dict]]:
    """Ответ без обращения к модели: перечень найденных норм.

    Поиск по документам идёт локально и ничего не стоит, поэтому при
    неподключённой модели (или исчерпанном балансе) незачем показывать голую
    ошибку — пользователю отдаются сами нормы, пусть и без формулировки.
    """
    search_query = contextualize(question, history or [])
    hits = get_store().search(search_query, top_k or settings.top_k)
    if not hits:
        return NO_CONTEXT_ANSWER, []

    lines = [SEARCH_ONLY_HEADER, ""]
    for hit in hits:
        reference = " · ".join(
            part for part in (hit["document"], hit.get("locator"), hit.get("chapter"))
            if part
        )
        lines.append(f"• {reference}")
    lines += ["", SEARCH_ONLY_FOOTER, "", DISCLAIMER]
    return "\n".join(lines), hits


# Ключ Anthropic выглядит так: sk-ant-<тип>-<длинная строка>.
KEY_PREFIX = "sk-ant-"
# Значение из шаблона .env.example — признак того, что ключ так и не вписали.
PLACEHOLDER_KEYS = {"sk-ant-...", "sk-ant-", "sk-ant-api03-..."}


def describe_key(key: str) -> str:
    """Безопасное описание ключа: видно, что лежит в .env, но не сам ключ."""
    if not key:
        return "пусто"
    head = key[:10]
    tail = key[-4:] if len(key) > 14 else ""
    return f"«{head}…{tail}», длина {len(key)}"


def inspect_key(key: str) -> Optional[str]:
    """Найти проблему в самом ключе, не обращаясь к Anthropic.

    Ошибку формата дешевле и понятнее поймать здесь, чем получить от API
    безликое «отклонил ключ».
    """
    if not key:
        return None
    if key in PLACEHOLDER_KEYS or key.endswith("..."):
        return (
            "В .env остался пример из шаблона, а не настоящий ключ "
            f"({describe_key(key)}). Создайте ключ на console.anthropic.com → "
            "Settings → API keys и вставьте его целиком."
        )
    if not key.startswith(KEY_PREFIX):
        return (
            f"Ключ в .env не похож на ключ Anthropic: {describe_key(key)}. "
            f"Настоящий начинается с {KEY_PREFIX!r}. Проверьте, что скопирована "
            "именно строка ключа, без лишних символов."
        )
    if any(ch.isspace() for ch in key):
        return (
            "Внутри ключа есть пробел или перенос строки — скорее всего он "
            "скопирован по частям. Вставьте ключ одной строкой."
        )
    if len(key) < 40:
        return (
            f"Ключ слишком короткий ({describe_key(key)}) — похоже, скопирован "
            "не полностью. Скопируйте его целиком из консоли Anthropic."
        )
    return None


def check_connection() -> dict:
    """Проверить, работает ли ключ Anthropic.

    Делает минимальный запрос к дешёвой модели (доли цента) и переводит ответ
    в понятный статус: администратору важно знать, почему ассистент молчит —
    ключ не вписан, ключ неверный или на счету нет средств.
    """
    key = settings.anthropic_api_key
    if not key:
        return {
            "status": "no_key",
            "message": "ANTHROPIC_API_KEY не указан в файле .env. "
            "Ключ создаётся на console.anthropic.com → Settings → API keys. "
            "После правки .env перезапустите сервер: файл читается при старте.",
            "model": settings.anthropic_model,
        }

    problem = inspect_key(key)
    if problem:
        return {"status": "bad_key_format", "message": problem, "model": settings.anthropic_model}

    try:
        _client().messages.create(
            model=CONTEXTUALIZE_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    except AuthenticationError:
        return {
            "status": "invalid_key",
            "message": "Anthropic отклонил ключ. Сервер прочитал из .env ключ "
            f"{describe_key(key)}. Если это не тот ключ, который вы вписали, — "
            "сервер не перезапущен после правки файла. Если тот — ключ отозван "
            "или скопирован не полностью, создайте новый на console.anthropic.com.",
            "model": settings.anthropic_model,
        }
    except PermissionDeniedError as exc:
        return {
            "status": "no_access",
            "message": "Ключ принят, но доступ к модели закрыт. Обычно это значит, "
            f"что на счету нет средств: пополните баланс в Anthropic Console. ({exc})",
            "model": settings.anthropic_model,
        }
    except RateLimitError:
        return {
            "status": "rate_limited",
            "message": "Ключ работает, но сейчас достигнут лимит запросов. "
            "Подождите минуту.",
            "model": settings.anthropic_model,
        }
    except APIConnectionError:
        return {
            "status": "no_network",
            "message": "Нет связи с Anthropic. Проверьте интернет и настройки прокси.",
            "model": settings.anthropic_model,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Проверка подключения к Anthropic не удалась")
        return {
            "status": "error",
            "message": f"Неожиданная ошибка при обращении к Anthropic: {exc}",
            "model": settings.anthropic_model,
        }
    return {
        "status": "ok",
        "message": f"Ключ работает, ответы формирует модель {settings.anthropic_model}.",
        "model": settings.anthropic_model,
    }


def answer_question(
    question: str,
    top_k: Optional[int] = None,
    history: Optional[List[dict]] = None,
) -> Tuple[str, List[dict], bool, dict]:
    """Вернуть (ответ, источники, признак наличия контекста, расход токенов).

    ``history`` — предыдущие реплики диалога в хронологическом порядке
    (``{"role": "user"|"assistant", "content": ...}``). Они и уточняют поиск,
    и передаются модели, чтобы разговор можно было продолжать.
    """
    history = history or []
    search_query = contextualize(question, history)
    hits = get_store().search(search_query, top_k or settings.top_k)
    if not hits:
        # Модель не вызывалась — и платить не за что.
        return NO_CONTEXT_ANSWER, [], False, {}

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
            "Anthropic отклонил ключ API. Проверьте ANTHROPIC_API_KEY в файле .env "
            "(ключ выдаётся на console.anthropic.com) и перезапустите сервер — "
            "файл .env читается один раз при старте."
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

    usage = {
        "model": settings.anthropic_model,
        "input_tokens": getattr(response.usage, "input_tokens", 0),
        "output_tokens": getattr(response.usage, "output_tokens", 0),
    }
    return answer, hits, True, usage
