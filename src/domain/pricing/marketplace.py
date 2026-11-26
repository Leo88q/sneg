"""Расчёт плановой комиссии площадки; не является платёжным или договорным модулем.

Новая модель пока не подключена к действующим заказам. Расчёт не подтверждает
получение денег, заключение договора, перечисление, доход или прибыль оператора.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from src.core.exceptions import ValidationAppError

COMMISSION_PERCENT = Decimal("27")
CENT = Decimal("0.01")
# В БД текущая стоимость заказа хранится как Numeric(12, 2).
MAX_ORDER_TOTAL = Decimal("9999999999.99")


@dataclass(frozen=True, slots=True)
class MarketplaceEstimate:
    total: Decimal
    commission_percent: Decimal
    commission: Decimal
    executor_after_commission: Decimal


def estimate_marketplace_commission(total: Decimal) -> MarketplaceEstimate:
    """27% от полной цены, округление комиссии до копеек, остаток — разницей.

    Доля исполнителя приведена до его налогов и расходов (включая аренду).
    Валидация намеренно не округляет вход: согласованная цена должна быть точной.
    """
    if not isinstance(total, Decimal) or not total.is_finite():
        raise ValidationAppError("Стоимость заказа должна быть конечным Decimal")
    if total <= 0 or total > MAX_ORDER_TOTAL:
        raise ValidationAppError("Стоимость заказа вне допустимого диапазона")
    if total != total.quantize(CENT):
        raise ValidationAppError("Стоимость заказа должна быть указана с точностью до копеек")
    total = total.quantize(CENT)
    commission = (total * COMMISSION_PERCENT / Decimal("100")).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return MarketplaceEstimate(
        total=total,
        commission_percent=COMMISSION_PERCENT,
        commission=commission,
        executor_after_commission=total - commission,
    )
