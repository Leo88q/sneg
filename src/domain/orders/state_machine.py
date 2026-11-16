"""Строгая статусная машина заказов (1-в-1 с ТЗ v2.0 B5.1 и B5.2)."""

from __future__ import annotations

from src.core.enums import OrderStatus
from src.core.exceptions import InvalidStateTransitionError

ALLOWED_ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {
        OrderStatus.AWAITING_ADVANCE,
        OrderStatus.CANCELED_BY_CLIENT,
    },
    OrderStatus.AWAITING_ADVANCE: {
        OrderStatus.SEARCHING_EXECUTOR,
        OrderStatus.CANCELED_TIMEOUT,
        OrderStatus.CANCELED_BY_CLIENT,
    },
    OrderStatus.SEARCHING_EXECUTOR: {
        OrderStatus.PARTIALLY_ASSIGNED,
        OrderStatus.ASSIGNED,
        OrderStatus.CANCELED_BY_CLIENT,
        OrderStatus.CANCELED_BY_SERVICE,
    },
    OrderStatus.PARTIALLY_ASSIGNED: {
        OrderStatus.ASSIGNED,
        OrderStatus.SEARCHING_EXECUTOR,
        OrderStatus.CANCELED_BY_CLIENT,
        OrderStatus.CANCELED_BY_SERVICE,
    },
    OrderStatus.ASSIGNED: {
        OrderStatus.EN_ROUTE,
        OrderStatus.SEARCHING_EXECUTOR,
        OrderStatus.CANCELED_BY_CLIENT,
        OrderStatus.CANCELED_BY_SERVICE,
    },
    OrderStatus.EN_ROUTE: {
        OrderStatus.ARRIVED,
        OrderStatus.SEARCHING_EXECUTOR,
        OrderStatus.CANCELED_BY_CLIENT,
        OrderStatus.CANCELED_BY_SERVICE,
    },
    OrderStatus.ARRIVED: {
        OrderStatus.IN_PROGRESS,
        OrderStatus.PRICE_ADJUSTMENT_PENDING,
        OrderStatus.CANCELED_BY_CLIENT,
        OrderStatus.CANCELED_BY_SERVICE,
    },
    OrderStatus.PRICE_ADJUSTMENT_PENDING: {
        OrderStatus.IN_PROGRESS,
        OrderStatus.ARRIVED,
        OrderStatus.CANCELED_BY_CLIENT,
        OrderStatus.CANCELED_BY_SERVICE,
    },
    OrderStatus.IN_PROGRESS: {
        OrderStatus.AWAITING_ACCEPTANCE,
        OrderStatus.DISPUTED,
        OrderStatus.CANCELED_BY_SERVICE,
    },
    OrderStatus.AWAITING_ACCEPTANCE: {
        OrderStatus.AWAITING_FINAL_PAYMENT,
        OrderStatus.COMPLETED,
        OrderStatus.DISPUTED,
    },
    OrderStatus.AWAITING_FINAL_PAYMENT: {
        OrderStatus.COMPLETED,
        OrderStatus.DISPUTED,
    },
    OrderStatus.DISPUTED: {
        OrderStatus.IN_PROGRESS,
        OrderStatus.AWAITING_FINAL_PAYMENT,
        OrderStatus.COMPLETED,
        OrderStatus.CANCELED_BY_SERVICE,
    },
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELED_TIMEOUT: set(),
    OrderStatus.CANCELED_BY_CLIENT: set(),
    OrderStatus.CANCELED_BY_SERVICE: set(),
}

TERMINAL_ORDER_STATUSES: set[OrderStatus] = {
    OrderStatus.COMPLETED,
    OrderStatus.CANCELED_TIMEOUT,
    OrderStatus.CANCELED_BY_CLIENT,
    OrderStatus.CANCELED_BY_SERVICE,
}


def can_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    return to_status in ALLOWED_ORDER_TRANSITIONS.get(from_status, set())


def assert_order_transition(from_status: OrderStatus, to_status: OrderStatus) -> None:
    if not can_transition(from_status, to_status):
        raise InvalidStateTransitionError(
            f"Недопустимый переход статуса заказа: {from_status.value} -> {to_status.value}",
            details={"from_status": from_status.value, "to_status": to_status.value},
        )
