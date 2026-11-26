from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from src.core.enums import OrderPaymentStatus, OrderStatus, PaymentStatus, RefundStatus
from src.infra.db.models import Order, Payment, Refund, utcnow
from tests.integration.test_marketplace_drafts import login
from tests.integration.test_payment_retries import create_payment

URL = "/api/v1/admin/reconciliation/orders"


@pytest.mark.asyncio
async def test_access_restricted_and_local_records_only(client: Any) -> None:
    http, container = client
    order, payment = await create_payment(container)
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        p = await session.get(Payment, payment.id)
        assert saved is not None and p is not None
        saved.status = OrderStatus.CANCELED_TIMEOUT.value
        saved.payment_status = OrderPaymentStatus.REFUNDED.value
        saved.paid_amount = Decimal("100.00")
        p.status = PaymentStatus.SUCCEEDED.value
        p.receipt_status = "issued"  # Legacy issued is not proof of a fiscal receipt.
        session.add(Refund(order_id=order.id, payment_id=p.id, idempotency_key=uuid.uuid4(),
                           amount=p.amount + Decimal("1"), reason="test", status=RefundStatus.SUCCEEDED.value))
        await session.commit()
    assert (await http.get(URL)).status_code == 401
    await login(http, "foreman")
    assert (await http.get(URL)).status_code == 403
    await login(http)
    result = await http.get(URL)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    data = result.json()
    assert data["provider_checked"] is False and data["scanned"] == 1
    item = data["items"][0]
    assert item["public_number"] == order.public_number
    assert {"paid_amount_mismatch", "receipt_requires_external_check", "refund_requires_external_check", "refund_exceeds_or_mismatches_payment"} <= set(item["issues"])
    assert "address" not in item and "phone" not in item
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        p = await session.get(Payment, payment.id)
        assert saved is not None and saved.paid_amount == Decimal("100.00")
        assert p is not None and p.receipt_status == "issued"


@pytest.mark.asyncio
async def test_expired_pending_canceled_and_pagination(client: Any) -> None:
    http, container = client
    first, payment = await create_payment(container)
    second, _ = await create_payment(container)
    async with container.session_factory() as session:
        order = await session.get(Order, first.id)
        p = await session.get(Payment, payment.id)
        assert order is not None and p is not None
        order.status = OrderStatus.CANCELED_BY_CLIENT.value
        p.expires_at = utcnow() - timedelta(days=1)
        p.provider_payment_id = None
        await session.commit()
    await login(http)
    page1 = (await http.get(URL, params={"limit": 1})).json()
    assert page1["scanned"] == 1 and page1["next_before"] == second.public_number
    page2 = (await http.get(URL, params={"limit": 1, "before": page1["next_before"]})).json()
    assert page2["scanned"] == 1 and page2["next_before"] is None
    assert page2["items"][0]["public_number"] == first.public_number
    assert {"canceled_order_with_pending_payment", "expired_payment_pending", "payment_missing_provider_id"} <= set(page2["items"][0]["issues"])
    assert (await http.get(URL, params={"limit": 101})).status_code == 422
    async with container.session_factory() as session:
        assert (await session.execute(select(Payment).where(Payment.id == payment.id))).scalar_one().status == "pending"


@pytest.mark.asyncio
async def test_canceled_order_with_unreturned_paid_amount(client: Any) -> None:
    http, container = client
    order, payment = await create_payment(container)
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        pay = await session.get(Payment, payment.id)
        assert saved is not None and pay is not None
        saved.status = OrderStatus.CANCELED_BY_SERVICE.value
        saved.paid_amount = pay.amount
        pay.status = PaymentStatus.SUCCEEDED.value
        await session.commit()
    await login(http)
    data = (await http.get(URL)).json()
    assert "canceled_order_with_unreturned_payment" in data["items"][0]["issues"]
