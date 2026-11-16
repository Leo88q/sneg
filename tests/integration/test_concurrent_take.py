from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from src.core.container import AppContainer
from src.core.enums import (
    DepthBucket,
    ExecutorStatus,
    OrderStatus,
    ServiceType,
    UrgencyLevel,
    UserRole,
)
from src.core.exceptions import OrderAlreadyTakenError
from src.domain.pricing.calculator import PriceCalculationInput
from src.infra.db.models import (
    ExecutorProfile,
    GeoZone,
    Order,
    OrderAssignment,
    SimulatorMessage,
    User,
)
from src.services.payments.mock import MockPaymentProvider


@pytest.mark.asyncio
async def test_20_way_concurrent_take_order_has_exactly_one_winner(
    container: AppContainer,
) -> None:
    """ADR-006 concurrency test: 20 executors hit 'Беру' simultaneously -> strictly 1 wins."""
    async with container.session_factory() as session:
        client_user = (
            await session.execute(select(User).where(User.max_user_id == 10001))
        ).scalar_one()
        zone = (
            await session.execute(select(GeoZone).where(GeoZone.code == "malakhovka"))
        ).scalar_one()

        exec_ids = []
        for idx in range(20):
            u = User(
                max_user_id=50000 + idx,
                role=UserRole.EXECUTOR.value,
                first_name=f"Исполнитель #{idx + 1}",
            )
            session.add(u)
            await session.flush()
            session.add(
                ExecutorProfile(
                    user_id=u.id,
                    status=ExecutorStatus.ACTIVE.value,
                    has_snowblower=True,
                )
            )
            exec_ids.append(u.id)
        await session.commit()
        client_uid = client_user.id
        zone_id = zone.id

    order, pay = await container.orchestrator.create_order_with_advance(
        client_user_id=client_uid,
        service_type=ServiceType.SNOW_YARD,
        geo_zone_id=zone_id,
        address_text="Малаховка, ул. Сосновая, д. 77",
        calc_input=PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD,
            area_m2=Decimal("100"),
            depth_bucket=DepthBucket.FROM_10_TO_30,
            urgency=UrgencyLevel.WITHIN_3H,
        ),
        params_json={"area_m2": "100"},
        lat=55.6435,
        lon=38.0068,
    )
    assert isinstance(container.payment_provider, MockPaymentProvider)
    assert pay.provider_payment_id is not None
    container.payment_provider.mark_status(pay.provider_payment_id, "succeeded")
    await container.orchestrator.confirm_payment_succeeded(pay.id)

    async def attempt_take(exec_uid):
        try:
            await container.orchestrator.take_order_atomic(
                order_id=order.id, executor_user_id=exec_uid
            )
            return True, exec_uid
        except OrderAlreadyTakenError:
            return False, exec_uid

    results = await asyncio.gather(*(attempt_take(uid) for uid in exec_ids))
    winners = [r for r in results if r[0]]
    losers = [r for r in results if not r[0]]

    assert len(winners) == 1, f"Expected strictly 1 winner, got {len(winners)}"
    assert len(losers) == 19

    # Verify DB integrity: strictly 1 assignment, status=assigned, dispatch card edited
    async with container.session_factory() as session:
        order_db = await session.get(Order, order.id)
        assert order_db is not None
        assert order_db.status == OrderStatus.ASSIGNED.value
        assert order_db.dispatch_message_id is not None

        cnt = (
            await session.execute(
                select(func.count())
                .select_from(OrderAssignment)
                .where(OrderAssignment.order_id == order.id)
            )
        ).scalar_one()
        assert cnt == 1

        dispatch_msg = (
            await session.execute(
                select(SimulatorMessage).where(
                    SimulatorMessage.max_message_id == order_db.dispatch_message_id
                )
            )
        ).scalar_one()
        assert dispatch_msg.is_edited is True
        assert "Забрал(и):" in dispatch_msg.text
        assert "Свободно слотов: 0" in dispatch_msg.text
