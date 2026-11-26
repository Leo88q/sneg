from decimal import Decimal

import pytest
from sqlalchemy import select

from src.core.container import AppContainer
from src.core.enums import DisputeStatus, MediaStage, OrderStatus, ServiceType, UserRole
from src.core.exceptions import (
    InvalidStateTransitionError,
    PermissionDeniedError,
    ValidationAppError,
)
from src.domain.pricing.calculator import PriceCalculationInput
from src.infra.db.models import Dispute, GeoZone, Order, OrderMedia, User


@pytest.mark.asyncio
async def test_dispute_and_media_cannot_be_controlled_by_other_users(
    container: AppContainer,
) -> None:
    async with container.session_factory() as session:
        client = (await session.execute(select(User).where(User.max_user_id == 10001))).scalar_one()
        foreman = (
            (await session.execute(select(User).where(User.role == UserRole.FOREMAN.value)))
            .scalars()
            .first()
        )
        zone = (
            await session.execute(select(GeoZone).where(GeoZone.code == "malakhovka"))
        ).scalar_one()
        stranger = User(max_user_id=987654321, role=UserRole.CLIENT.value, first_name="Посторонний")
        session.add(stranger)
        await session.commit()
        assert foreman is not None
        client_id, stranger_id, foreman_id, zone_id = client.id, stranger.id, foreman.id, zone.id

    order, _ = await container.orchestrator.create_order_with_advance(
        client_user_id=client_id,
        service_type=ServiceType.SNOW_YARD,
        geo_zone_id=zone_id,
        address_text="Тестовая улица, дом 1",
        calc_input=PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD, area_m2=Decimal("100")
        ),
        params_json={},
    )
    async with container.session_factory() as session:
        stored_order = await session.get(Order, order.id)
        assert stored_order is not None
        stored_order.status = OrderStatus.AWAITING_ACCEPTANCE.value
        await session.commit()

    with pytest.raises(PermissionDeniedError):
        await container.orchestrator.open_dispute(
            order_id=order.id, opener_user_id=stranger_id, reason="чужой заказ"
        )
    with pytest.raises(PermissionDeniedError):
        await container.orchestrator.upload_order_media(
            order_id=order.id,
            uploader_user_id=stranger_id,
            stage=MediaStage.BEFORE,
            data=b"image",
            filename="test.jpg",
        )
    async with container.session_factory() as session:
        assert (await session.execute(select(OrderMedia))).scalars().all() == []
        stored_order = await session.get(Order, order.id)
        assert (
            stored_order is not None
            and stored_order.status == OrderStatus.AWAITING_ACCEPTANCE.value
        )

    dispute = await container.orchestrator.open_dispute(
        order_id=order.id, opener_user_id=client_id, reason="Проблема с уборкой"
    )
    # Поддельная кнопка из MAX не должна позволять постороннему закрыть спор.
    await container.bot_router.handle_update(
        {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "forged-rework",
                "payload": f"disp:rework:{dispute.id}",
                "user": {"user_id": 987654321, "first_name": "Посторонний"},
            },
            "message": {"recipient": {"chat_id": 987654321, "chat_type": "dialog"}},
        }
    )
    async with container.session_factory() as session:
        saved = await session.get(Dispute, dispute.id)
        assert saved is not None and saved.status == DisputeStatus.OPEN.value
    with pytest.raises(PermissionDeniedError):
        await container.orchestrator.resolve_dispute(
            dispute_id=dispute.id,
            resolution=DisputeStatus.RESOLVED_REWORK,
            note="нет",
            actor_user_id=stranger_id,
        )
    with pytest.raises(PermissionDeniedError):
        await container.orchestrator.resolve_dispute(
            dispute_id=dispute.id,
            resolution=DisputeStatus.RESOLVED_REFUND,
            note="нет",
            actor_user_id=foreman_id,
        )
    with pytest.raises(ValidationAppError):
        await container.orchestrator.resolve_dispute(
            dispute_id=dispute.id,
            resolution=DisputeStatus.OPEN,
            note="нет",
            actor_user_id=foreman_id,
        )
    resolved = await container.orchestrator.resolve_dispute(
        dispute_id=dispute.id,
        resolution=DisputeStatus.RESOLVED_REWORK,
        note="Исправить",
        actor_user_id=foreman_id,
    )
    assert resolved.status == DisputeStatus.RESOLVED_REWORK.value
    with pytest.raises(InvalidStateTransitionError):
        await container.orchestrator.resolve_dispute(
            dispute_id=dispute.id,
            resolution=DisputeStatus.RESOLVED_REWORK,
            note="повтор",
            actor_user_id=foreman_id,
        )
