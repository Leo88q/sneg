"""Read-only local financial exception queue; not a provider reconciliation."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select

from src.api.deps import get_container, require_roles
from src.core.container import AppContainer
from src.core.enums import AdminRole, OrderPaymentStatus, OrderStatus, PaymentStatus, RefundStatus
from src.infra.db.models import AdminAccount, Order, Payment, Refund, utcnow

router = APIRouter(prefix="/api/v1/admin/reconciliation", tags=["reconciliation"])
ZERO = Decimal("0.00")
CANCELED = {
    OrderStatus.CANCELED_BY_CLIENT.value,
    OrderStatus.CANCELED_BY_SERVICE.value,
    OrderStatus.CANCELED_TIMEOUT.value,
}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _examine(order: Order, payments: list[Payment], refunds: list[Refund], now: datetime) -> dict[str, Any] | None:
    paid = sum((p.amount for p in payments if p.status == PaymentStatus.SUCCEEDED.value), ZERO)
    returned = sum((r.amount for r in refunds if r.status == RefundStatus.SUCCEEDED.value), ZERO)
    issues: list[str] = []
    if order.paid_amount != paid:
        issues.append("paid_amount_mismatch")
    if order.status in CANCELED and paid > returned:
        issues.append("canceled_order_with_unreturned_payment")
    if order.status in CANCELED and any(p.status == PaymentStatus.PENDING.value for p in payments):
        issues.append("canceled_order_with_pending_payment")
    if any(p.status == PaymentStatus.PENDING.value and p.expires_at is not None and _utc(p.expires_at) <= now for p in payments):
        issues.append("expired_payment_pending")
    if any(p.status != PaymentStatus.CANCELED.value and not p.provider_payment_id for p in payments):
        issues.append("payment_missing_provider_id")
    # Even historical 'issued' was formerly set without fiscal-provider confirmation.
    if any(p.status == PaymentStatus.SUCCEEDED.value for p in payments):
        issues.append("receipt_requires_external_check")
    # No external refund reconciliation is implemented, even for local succeeded.
    if refunds:
        issues.append("refund_requires_external_check")
    if order.payment_status in (
        OrderPaymentStatus.REFUNDED.value, OrderPaymentStatus.PARTIALLY_REFUNDED.value
    ) and (
        returned <= ZERO or (order.payment_status == OrderPaymentStatus.REFUNDED.value and returned < paid)
    ):
        issues.append("refund_status_not_supported_by_records")
    payments_by_id = {p.id: p for p in payments}
    sums: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for ref in refunds:
        if ref.status == RefundStatus.SUCCEEDED.value:
            sums[ref.payment_id] += ref.amount
    if any(pid not in payments_by_id or payments_by_id[pid].status != PaymentStatus.SUCCEEDED.value or amount > payments_by_id[pid].amount for pid, amount in sums.items()):
        issues.append("refund_exceeds_or_mismatches_payment")
    if not issues:
        return None
    return {
        "order_id": str(order.id), "public_number": order.public_number,
        "order_status": order.status, "payment_status": order.payment_status,
        "order_paid": str(order.paid_amount), "recorded_paid": str(paid),
        "recorded_refunded": str(returned), "issues": issues,
    }


@router.get("/orders")
async def reconciliation_orders(
    response: Response,
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=100),
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Examine a bounded page of orders, including older pages via public number."""
    response.headers["Cache-Control"] = "no-store"
    async with container.session_factory() as session:
        stmt = select(Order).order_by(Order.public_number.desc()).limit(limit + 1)
        if before is not None:
            stmt = stmt.where(Order.public_number < before)
        rows = list((await session.execute(stmt)).scalars().all())
        has_more = len(rows) > limit
        orders = rows[:limit]
        ids = [o.id for o in orders]
        payment_map: dict[uuid.UUID, list[Payment]] = defaultdict(list)
        refund_map: dict[uuid.UUID, list[Refund]] = defaultdict(list)
        if ids:
            for payment in (await session.execute(select(Payment).where(Payment.order_id.in_(ids)))).scalars():
                if payment.order_id is not None:
                    payment_map[payment.order_id].append(payment)
            for refund in (await session.execute(select(Refund).where(Refund.order_id.in_(ids)))).scalars():
                refund_map[refund.order_id].append(refund)
        now = utcnow()
        items = [item for order in orders if (item := _examine(order, payment_map[order.id], refund_map[order.id], now)) is not None]
    return {
        "scanned": len(orders), "items": items,
        "next_before": orders[-1].public_number if has_more and orders else None,
        "provider_checked": False,
    }
