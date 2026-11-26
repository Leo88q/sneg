"""Предварительная математика сценариев оплаты, не платёжный шлюз.

Не подключена к заказам, интерфейсам или каким-либо расчётам с пользователями.
План не подтверждает согласие сторон, полномочия на приём денег или выплаты.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from src.core.exceptions import ValidationAppError
from src.domain.pricing.marketplace import MarketplaceEstimate, estimate_marketplace_commission


class PaymentSchedule(StrEnum):
    ADVANCE_27 = "advance_27"
    FULL_UPFRONT = "full_upfront"
    AFTER_COMPLETION = "after_completion"


class FinalPaymentRoute(StrEnum):
    PAYMENT_PROVIDER = "payment_provider"
    DIRECT_TO_EXECUTOR = "direct_to_executor"


@dataclass(frozen=True, slots=True)
class PaymentPlan:
    estimate: MarketplaceEstimate
    schedule: PaymentSchedule
    upfront: Decimal
    after_completion: Decimal
    final_payment_route: FinalPaymentRoute | None


def estimate_payment_plan(
    total: Decimal,
    *,
    schedule: PaymentSchedule,
    final_payment_route: FinalPaymentRoute | None = None,
) -> PaymentPlan:
    """Считает суммы клиента, не определяя получателя предоплаты или зачёт комиссии.

    Для вариантов с постоплатой путь остатка должен быть выбран явно. Наличие
    маршрута не означает, что партнёр поддерживает схему или что она разрешена.
    """
    if not isinstance(schedule, PaymentSchedule):
        raise ValidationAppError("Неизвестный график оплаты")
    if schedule is PaymentSchedule.FULL_UPFRONT:
        if final_payment_route is not None:
            raise ValidationAppError("При полной предоплате остатка после работ нет")
    elif not isinstance(final_payment_route, FinalPaymentRoute):
        raise ValidationAppError("Необходимо выбрать способ оплаты после выполнения")

    estimate = estimate_marketplace_commission(total)
    if schedule is PaymentSchedule.ADVANCE_27:
        if estimate.commission == 0:
            raise ValidationAppError("Аванс 27% должен составлять не менее одной копейки")
        upfront = estimate.commission
    elif schedule is PaymentSchedule.FULL_UPFRONT:
        upfront = estimate.total
    else:
        upfront = Decimal("0.00")
    return PaymentPlan(
        estimate=estimate,
        schedule=schedule,
        upfront=upfront,
        after_completion=estimate.total - upfront,
        final_payment_route=final_payment_route,
    )
