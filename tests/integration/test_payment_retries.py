from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.config import Settings
from src.core.container import AppContainer
from src.core.enums import ServiceType
from src.core.exceptions import ExternalIntegrationError
from src.domain.pricing.calculator import PriceCalculationInput
from src.infra.db.models import GeoZone, Order, Payment, ProcessedWebhookEvent, User
from src.main import create_app
from src.services.payments.mock import MockPaymentProvider


async def create_payment(container: AppContainer) -> tuple[Order, Payment]:
    async with container.session_factory() as session:
        client = (await session.execute(select(User).where(User.max_user_id == 10001))).scalar_one()
        zone = (
            await session.execute(select(GeoZone).where(GeoZone.code == "malakhovka"))
        ).scalar_one()
    return await container.orchestrator.create_order_with_advance(
        client_user_id=client.id,
        service_type=ServiceType.SNOW_YARD,
        geo_zone_id=zone.id,
        address_text="Тестовая улица, дом 1",
        calc_input=PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD, area_m2=Decimal("100")
        ),
        params_json={},
    )


async def event_count(container: AppContainer) -> int:
    async with container.session_factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ProcessedWebhookEvent)
                    .where(ProcessedWebhookEvent.source == "yookassa")
                )
            ).scalar_one()
        )


@pytest.mark.asyncio
async def test_retry_after_provider_failure_is_not_dropped(client: Any, monkeypatch: Any) -> None:
    http, container = client
    order, payment = await create_payment(container)
    provider = container.payment_provider
    assert isinstance(provider, MockPaymentProvider)
    assert payment.provider_payment_id is not None
    provider.mark_status(payment.provider_payment_id, "succeeded")
    original = provider.get_payment
    calls = 0

    async def flaky(payment_id: str) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExternalIntegrationError("transient failure")
        return await original(payment_id)

    monkeypatch.setattr(provider, "get_payment", flaky)
    body = {"event": "payment.succeeded", "object": {"id": payment.provider_payment_id}}
    assert (await http.post("/webhooks/yookassa", json=body)).status_code == 502
    assert await event_count(container) == 0
    assert (await http.post("/webhooks/yookassa", json=body)).status_code == 200
    assert (await http.post("/webhooks/yookassa", json=body)).status_code == 200
    assert calls == 2
    assert await event_count(container) == 1
    async with container.session_factory() as session:
        saved_order = await session.get(Order, order.id)
        saved_payment = await session.get(Payment, payment.id)
        assert saved_order is not None and saved_order.paid_amount == payment.amount
        assert saved_payment is not None and saved_payment.status == "succeeded"
        assert saved_payment.receipt_status != "issued"


@pytest.mark.asyncio
async def test_retry_after_notification_failure_does_not_double_credit(
    client: Any, monkeypatch: Any
) -> None:
    http, container = client
    order, payment = await create_payment(container)
    provider = container.payment_provider
    assert isinstance(provider, MockPaymentProvider) and payment.provider_payment_id
    provider.mark_status(payment.provider_payment_id, "succeeded")

    async def fail_notification(*args: Any, **kwargs: Any) -> None:
        raise ExternalIntegrationError("MAX unavailable")

    monkeypatch.setattr(
        container.orchestrator, "_publish_or_update_dispatch_card", fail_notification
    )
    body = {"event": "payment.succeeded", "object": {"id": payment.provider_payment_id}}
    assert (await http.post("/webhooks/yookassa", json=body)).status_code == 502
    assert await event_count(container) == 0
    # Платёж уже сохранён, повтор не должен зачислить его второй раз.
    assert (await http.post("/webhooks/yookassa", json=body)).status_code == 200
    assert await event_count(container) == 1
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        assert saved is not None and saved.paid_amount == payment.amount


@pytest.mark.asyncio
async def test_unconfirmed_payment_retried_and_concurrent_events_are_idempotent(
    client: Any,
) -> None:
    http, container = client
    order, payment = await create_payment(container)
    provider = container.payment_provider
    assert isinstance(provider, MockPaymentProvider) and payment.provider_payment_id
    body = {"event": "payment.succeeded", "object": {"id": payment.provider_payment_id}}
    assert (await http.post("/webhooks/yookassa", json=body)).status_code == 503
    assert await event_count(container) == 0
    provider.mark_status(payment.provider_payment_id, "succeeded")
    responses = await asyncio.gather(
        *[http.post("/webhooks/yookassa", json=body) for _ in range(5)]
    )
    assert all(r.status_code == 200 for r in responses)
    assert await event_count(container) == 1
    async with container.session_factory() as session:
        saved = await session.get(Order, order.id)
        assert saved is not None and saved.paid_amount == payment.amount


@pytest.mark.asyncio
async def test_mock_payment_routes_never_exposed_in_staging(test_settings: Settings) -> None:
    settings = test_settings.model_copy(update={"app_env": "staging", "enable_simulator": False})
    app = create_app(settings=settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        assert (await http.get("/mock-pay/00000000-0000-0000-0000-000000000000")).status_code == 404
        assert (
            await http.post("/mock-pay/00000000-0000-0000-0000-000000000000/complete", json={})
        ).status_code == 404
    await app.state.container.shutdown()
