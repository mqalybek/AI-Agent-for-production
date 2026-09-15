"""Цены моделей Anthropic и оценка стоимости запросов.

Расход токенов от выбора модели не зависит: в модель уходит один и тот же
вопрос с теми же фрагментами документов. Меняется только цена за токен —
поэтому счёт считается по одной формуле с разными коэффициентами.

Цены — доллары за миллион токенов, по состоянию на июнь 2026. Актуальные:
https://www.anthropic.com/pricing
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

# модель -> (вход $/1M, выход $/1M)
PRICING: Dict[str, Tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}


def price_of(model: str) -> Optional[Tuple[float, float]]:
    """Цена модели или None, если модель нам неизвестна."""
    return PRICING.get(model)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """Стоимость запроса в долларах. None — если цена модели неизвестна."""
    price = price_of(model)
    if price is None:
        return None
    input_price, output_price = price
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def format_cost(amount: Optional[float]) -> str:
    """Человекочитаемая сумма: доллары для заметных трат, центы для мелких."""
    if amount is None:
        return "—"
    if amount >= 1:
        return f"${amount:.2f}"
    return f"{amount * 100:.1f} цента"
