from decimal import Decimal

import pytest

from src.core.exceptions import ValidationAppError
from src.domain.pricing.payment_plan import (
    FinalPaymentRoute,
    PaymentSchedule,
    estimate_payment_plan,
)


@pytest.mark.parametrize("route", list(FinalPaymentRoute))
def test_advance_equals_commission_and_rest_is_due_after_completion(route: FinalPaymentRoute) -> None:
    plan = estimate_payment_plan(
        Decimal("10000.00"), schedule=PaymentSchedule.ADVANCE_27, final_payment_route=route
    )
    assert plan.upfront == Decimal("2700.00")
    assert plan.after_completion == Decimal("7300.00")
    assert plan.final_payment_route == route
    assert plan.upfront + plan.after_completion == plan.estimate.total


@pytest.mark.parametrize("route", list(FinalPaymentRoute))
def test_full_payment_after_completion(route: FinalPaymentRoute) -> None:
    plan = estimate_payment_plan(
        Decimal("10000"), schedule=PaymentSchedule.AFTER_COMPLETION, final_payment_route=route
    )
    assert plan.upfront == Decimal("0.00")
    assert plan.after_completion == Decimal("10000.00")
    assert plan.estimate.commission == Decimal("2700.00")


def test_full_upfront_has_no_final_payment() -> None:
    plan = estimate_payment_plan(Decimal("10000"), schedule=PaymentSchedule.FULL_UPFRONT)
    assert plan.upfront == Decimal("10000.00")
    assert plan.after_completion == Decimal("0.00")
    assert plan.final_payment_route is None


@pytest.mark.parametrize("schedule", [PaymentSchedule.ADVANCE_27, PaymentSchedule.AFTER_COMPLETION])
def test_final_route_is_required(schedule: PaymentSchedule) -> None:
    with pytest.raises(ValidationAppError):
        estimate_payment_plan(Decimal("100"), schedule=schedule)


def test_no_final_route_allowed_for_full_upfront() -> None:
    with pytest.raises(ValidationAppError):
        estimate_payment_plan(
            Decimal("100"),
            schedule=PaymentSchedule.FULL_UPFRONT,
            final_payment_route=FinalPaymentRoute.DIRECT_TO_EXECUTOR,
        )


def test_invalid_schedule_and_zero_advance_rejected() -> None:
    with pytest.raises(ValidationAppError):
        estimate_payment_plan(Decimal("100"), schedule="advance_27")  # type: ignore[arg-type]
    with pytest.raises(ValidationAppError):
        estimate_payment_plan(
            Decimal("100"),
            schedule=PaymentSchedule.ADVANCE_27,
            final_payment_route="cash",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationAppError):
        estimate_payment_plan(
            Decimal("0.01"),
            schedule=PaymentSchedule.ADVANCE_27,
            final_payment_route=FinalPaymentRoute.PAYMENT_PROVIDER,
        )


def test_commission_and_residual_are_rounded_without_losing_kopecks() -> None:
    plan = estimate_payment_plan(
        Decimal("10350"),
        schedule=PaymentSchedule.ADVANCE_27,
        final_payment_route=FinalPaymentRoute.DIRECT_TO_EXECUTOR,
    )
    assert plan.upfront == Decimal("2794.50")
    assert plan.after_completion == Decimal("7555.50")
