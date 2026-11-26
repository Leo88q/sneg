from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from src.core.container import AppContainer
from src.core.enums import DisputeStatus, OrderStatus, PriceAdjustmentStatus, UserRole
from src.core.exceptions import InvalidStateTransitionError, PermissionDeniedError
from src.infra.db.models import (
    AdminAccount,
    Dispute,
    Order,
    Payment,
    PriceAdjustment,
    Refund,
    User,
    utcnow,
)
from tests.integration.test_payment_retries import create_payment


@pytest.mark.asyncio
async def test_paid_cancellation_and_refund_fail_closed(client: Any, monkeypatch: Any) -> None:
    http, container = client
    order, payment = await create_payment(container)
    provider_calls = []

    async def refund(*args: Any, **kwargs: Any) -> None:
        provider_calls.append((args, kwargs))
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(container.payment_provider, "create_refund", refund)
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        assert saved is not None
        saved.paid_amount = payment.amount
        saved.status = OrderStatus.SEARCHING_EXECUTOR.value
        await session.commit()
        client_id = saved.client_id
        before_payment_status = saved.payment_status

    await http.post(
        "/api/v1/admin/auth/login", json={"email": "owner@sneg.local", "password": "admin12345"}
    )
    response = await http.post(
        f"/api/v1/admin/orders/{order.id}/refund", json={"amount": "100", "reason": "тест"}
    )
    assert response.status_code == 409
    assert "временно недоступен" in response.text
    response = await http.post(
        f"/api/v1/admin/orders/{order.id}/cancel", json={"reason": "тест", "by_service": True}
    )
    assert response.status_code == 409
    with pytest.raises(InvalidStateTransitionError):
        await container.orchestrator.cancel_order(
            order_id=order.id, by_service=False, reason="тест", actor_user_id=client_id
        )
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        assert saved is not None
        assert saved.status == OrderStatus.SEARCHING_EXECUTOR.value
        assert saved.payment_status == before_payment_status
        assert saved.paid_amount == payment.amount
        assert (await session.execute(select(Refund))).scalars().all() == []
    assert provider_calls == []


@pytest.mark.asyncio
async def test_dispute_refund_rolls_back_every_change(container: AppContainer, monkeypatch: Any) -> None:
    order, payment = await create_payment(container)
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        assert saved is not None
        saved.status = OrderStatus.DISPUTED.value
        saved.paid_amount = payment.amount
        admin = (await session.execute(select(AdminAccount))).scalars().first()
        assert admin is not None
        dispute = Dispute(order_id=order.id, opened_by=saved.client_id, reason="тест")
        session.add(dispute)
        await session.commit()
        dispute_id, actor_id = dispute.id, admin.id

    async def refund(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(container.payment_provider, "create_refund", refund)
    for resolution in (DisputeStatus.RESOLVED_REFUND, DisputeStatus.RESOLVED_DISCOUNT):
        with pytest.raises(InvalidStateTransitionError):
            await container.orchestrator.resolve_dispute(
                dispute_id=dispute_id, resolution=resolution, note="тест", admin_id=actor_id,
                refund_amount=Decimal("100"), discount_amount=Decimal("10"),
            )
        async with container.session_factory() as session:
            saved = await session.get(Order, order.id)
            stored_dispute = await session.get(Dispute, dispute_id)
            assert saved is not None and saved.status == OrderStatus.DISPUTED.value
            assert saved.final_total == order.final_total
            assert stored_dispute is not None and stored_dispute.status == DisputeStatus.OPEN.value
            assert (await session.execute(select(Refund))).scalars().all() == []
    # A discount that makes an overpayment cannot close the order without a refund.
    with pytest.raises(InvalidStateTransitionError):
        await container.orchestrator.resolve_dispute(
            dispute_id=dispute_id, resolution=DisputeStatus.RESOLVED_DISCOUNT,
            note="тест", admin_id=actor_id, discount_amount=order.final_total,
        )
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        stored_dispute = await session.get(Dispute, dispute_id)
        assert saved is not None and saved.status == OrderStatus.DISPUTED.value
        assert stored_dispute is not None and stored_dispute.status == DisputeStatus.OPEN.value


@pytest.mark.asyncio
async def test_adjustment_cancel_checks_client_and_does_not_commit_on_failure(
    container: AppContainer,
) -> None:
    order, payment = await create_payment(container)
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        assert saved is not None
        saved.status = OrderStatus.PRICE_ADJUSTMENT_PENDING.value
        saved.paid_amount = payment.amount
        stranger = User(max_user_id=939393, role=UserRole.CLIENT.value, first_name="Чужой")
        session.add(stranger)
        adj = PriceAdjustment(
            order_id=order.id, proposed_by=saved.client_id, old_total=order.final_total,
            new_total=order.final_total + Decimal("100"), delta_pct=Decimal("1"),
            reason="тест", expires_at=utcnow() + timedelta(hours=1),
        )
        session.add(adj)
        await session.commit()
        stranger_id, client_id, adj_id = stranger.id, saved.client_id, adj.id

    with pytest.raises(PermissionDeniedError):
        await container.orchestrator.respond_price_adjustment(
            adjustment_id=adj_id, client_user_id=stranger_id, decision="cancel"
        )
    with pytest.raises(InvalidStateTransitionError):
        await container.orchestrator.respond_price_adjustment(
            adjustment_id=adj_id, client_user_id=client_id, decision="cancel"
        )
    async with container.session_factory() as session:
        adj = await session.get(PriceAdjustment, adj_id)
        saved = await session.get(Order, order.id)
        assert adj is not None and adj.status == PriceAdjustmentStatus.PENDING.value
        assert adj.client_responded_at is None
        assert saved is not None and saved.status == OrderStatus.PRICE_ADJUSTMENT_PENDING.value
        saved.paid_amount = Decimal("0")
        await session.commit()
    # A pending payment still prevents cancellation, even if paid_amount is zero.
    with pytest.raises(InvalidStateTransitionError):
        await container.orchestrator.respond_price_adjustment(
            adjustment_id=adj_id, client_user_id=client_id, decision="cancel"
        )
    async with container.session_factory() as session:
        stored_payment = await session.get(Payment, payment.id)
        assert stored_payment is not None
        await session.delete(stored_payment)
        await session.commit()

    await container.orchestrator.respond_price_adjustment(
        adjustment_id=adj_id, client_user_id=client_id, decision="cancel"
    )
    async with container.session_factory() as session:
        adj = await session.get(PriceAdjustment, adj_id)
        saved = await session.get(Order, order.id)
        assert adj is not None and adj.status == PriceAdjustmentStatus.REJECTED.value
        assert saved is not None and saved.status == OrderStatus.CANCELED_BY_CLIENT.value


@pytest.mark.asyncio
async def test_expiry_timer_never_infers_payment_cancellation(container: AppContainer) -> None:
    order, payment = await create_payment(container)
    result = await container.orchestrator.run_expired_timers(force_all=True)
    assert result["expired_unpaid"] == 0
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        stored_payment = await session.get(Payment, payment.id)
        assert saved is not None and saved.status == OrderStatus.AWAITING_ADVANCE.value
        assert stored_payment is not None and stored_payment.status == "pending"
