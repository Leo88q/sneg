from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from src.core.config import Settings
from src.core.container import AppContainer
from src.core.enums import OrderStatus, ServiceType
from src.core.exceptions import ValidationAppError
from src.infra.db.models import Order, ProcessedWebhookEvent, User
from src.services.messenger.base import InlineButton, InlineKeyboard, validate_max_keyboard
from src.services.payments.base import CreatePaymentCommand, ReceiptItem
from src.services.payments.mock import MockPaymentProvider
from src.services.payments.yookassa import YooKassaProvider


def test_production_guards_forbid_mock_and_simulator() -> None:
    with pytest.raises(ValueError, match="ENABLE_SIMULATOR"):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://u:p@db/sneg",
            redis_url="redis://redis:6379/0",
            enable_simulator=True,
            messenger_transport="max",
            payment_provider="yookassa",
            max_bot_token="real-token",
            max_webhook_secret="real-secret-12345",
            admin_jwt_secret="x" * 40,
            pii_encryption_key="y" * 32,
        )

    with pytest.raises(ValueError, match="PAYMENT_PROVIDER=mock"):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://u:p@db/sneg",
            redis_url="redis://redis:6379/0",
            enable_simulator=False,
            messenger_transport="max",
            payment_provider="mock",
            max_bot_token="real-token",
            max_webhook_secret="real-secret-12345",
            admin_jwt_secret="x" * 40,
            pii_encryption_key="y" * 32,
        )


def test_max_inline_keyboard_validation_rules() -> None:
    # 4 special buttons in a single row must raise ValidationAppError (MAX limit is 3)
    bad_kb = InlineKeyboard(
        rows=[
            [
                InlineButton(type="link", text=f"Link {i}", url="https://example.com")
                for i in range(4)
            ]
        ]
    )
    with pytest.raises(ValidationAppError):
        validate_max_keyboard(bad_kb)

    # 3 special buttons in one row is allowed
    ok_kb = InlineKeyboard(
        rows=[
            [
                InlineButton(type="link", text=f"Link {i}", url="https://example.com")
                for i in range(3)
            ]
        ]
    )
    validate_max_keyboard(ok_kb)


def test_yookassa_ip_allowlist_and_54fz_receipt_builder() -> None:
    provider = YooKassaProvider(
        shop_id="123456",
        secret_key="test_secret",
        vat_code=1,
        tax_system_code=2,
        trusted_cidrs=[
            "185.71.76.0/27",
            "77.75.156.11/32",
            "2a02:5180::/32",
        ],
    )
    assert provider.verify_webhook_ip("185.71.76.10") is True
    assert provider.verify_webhook_ip("77.75.156.11") is True
    assert provider.verify_webhook_ip("2a02:5180::1") is True
    assert provider.verify_webhook_ip("8.8.8.8") is False

    cmd = CreatePaymentCommand(
        internal_payment_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4(),
        amount=Decimal("1550.00"),
        description="Аванс по заказу #1001",
        return_url="https://sneg.example.ru/pay/return",
        customer_phone="+7 999 111-22-33",
        receipt_items=[
            ReceiptItem(
                description="Аванс за уборку снега #1001",
                amount=Decimal("1550.00"),
                payment_mode="partial_prepayment",
                payment_subject="service",
            )
        ],
    )
    receipt = provider._build_receipt(cmd)
    assert receipt is not None
    assert receipt["internet"] is True
    assert receipt["tax_system_code"] == 2
    assert receipt["customer"]["phone"] == "79991112233"
    assert receipt["items"][0]["payment_mode"] == "partial_prepayment"
    assert receipt["items"][0]["payment_subject"] == "service"
    assert receipt["items"][0]["amount"]["value"] == "1550.00"


@pytest.mark.asyncio
async def test_max_webhook_secret_and_idempotency(
    client: tuple[httpx.AsyncClient, AppContainer],
) -> None:
    ac, container = client
    payload = {
        "update_id": "upd-idempotent-1",
        "update_type": "bot_started",
        "chat_id": 10001,
        "user": {"user_id": 10001, "name": "Анна"},
    }
    # Bad secret -> 401
    r_bad = await ac.post("/webhooks/max", json=payload, headers={"X-Max-Bot-Api-Secret": "wrong"})
    assert r_bad.status_code == 401

    # Valid secret -> 200
    r_ok = await ac.post(
        "/webhooks/max", json=payload, headers={"X-Max-Bot-Api-Secret": "test-max-secret-12345"}
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["ok"] is True

    # Replay same update -> deduplicated (no duplicate event row or duplicate reply)
    r_dup = await ac.post(
        "/webhooks/max", json=payload, headers={"X-Max-Bot-Api-Secret": "test-max-secret-12345"}
    )
    assert r_dup.status_code == 200

    async with container.session_factory() as session:
        cnt = (
            await session.execute(
                select(func.count())
                .select_from(ProcessedWebhookEvent)
                .where(ProcessedWebhookEvent.source == "max")
            )
        ).scalar_one()
        assert cnt == 1


@pytest.mark.asyncio
async def test_max_request_contact_isolation_rule(
    client: tuple[httpx.AsyncClient, AppContainer],
) -> None:
    """Phone from MAX request_contact goes ONLY to bot_phone, NEVER shared_contact_phone."""
    ac, container = client
    r = await ac.post(
        "/api/v1/simulator/send-message",
        json={"persona": "client_1", "contact_phone": "+7 999 777-88-99"},
    )
    assert r.status_code == 200

    async with container.session_factory() as session:
        user = (
            await session.execute(select(User).where(User.max_user_id == 10001))
        ).scalar_one()
        assert user.bot_phone is not None
        assert "777-88-99" in user.bot_phone
        assert user.shared_contact_phone is None


@pytest.mark.asyncio
async def test_yookassa_webhook_verifies_via_provider_and_publishes_order(
    client: tuple[httpx.AsyncClient, AppContainer],
) -> None:
    ac, container = client
    from src.core.enums import DepthBucket, UrgencyLevel
    from src.domain.pricing.calculator import PriceCalculationInput
    from src.infra.db.models import GeoZone

    async with container.session_factory() as session:
        user = (await session.execute(select(User).where(User.max_user_id == 10001))).scalar_one()
        zone = (
            await session.execute(select(GeoZone).where(GeoZone.code == "malakhovka"))
        ).scalar_one()
        uid, zid = user.id, zone.id

    order, pay = await container.orchestrator.create_order_with_advance(
        client_user_id=uid,
        service_type=ServiceType.SNOW_YARD,
        geo_zone_id=zid,
        address_text="Малаховка, ул. Тестовая 1",
        calc_input=PriceCalculationInput(
            service_type=ServiceType.SNOW_YARD,
            area_m2=Decimal("120"),
            depth_bucket=DepthBucket.FROM_10_TO_30,
            urgency=UrgencyLevel.WITHIN_3H,
        ),
        params_json={"area_m2": "120"},
    )
    assert isinstance(container.payment_provider, MockPaymentProvider)
    assert pay.provider_payment_id is not None
    container.payment_provider.mark_status(pay.provider_payment_id, "succeeded")

    resp = await ac.post(
        "/webhooks/yookassa",
        json={"event": "payment.succeeded", "object": {"id": pay.provider_payment_id}},
    )
    assert resp.status_code == 200

    async with container.session_factory() as session:
        refreshed = await session.get(Order, order.id)
        assert refreshed is not None
        assert refreshed.status == OrderStatus.SEARCHING_EXECUTOR.value
        assert refreshed.paid_amount == pay.amount


def test_max_setup_validator() -> None:
    from src.bot.setup_max import validate_max_setup_settings

    # Пустой токен -> ошибка
    with pytest.raises(ValueError, match="MAX_BOT_TOKEN"):
        validate_max_setup_settings(
            Settings(
                max_bot_token="",
                public_base_url="https://chistodvor.ru",
                max_webhook_secret="prod_secret_987654",
            )
        )

    # HTTP вместо HTTPS -> ошибка (MAX с 25.05.2026 требует только HTTPS)
    with pytest.raises(ValueError, match="https://"):
        validate_max_setup_settings(
            Settings(
                max_bot_token="real-max-token",
                public_base_url="http://chistodvor.ru",
                max_webhook_secret="prod_secret_987654",
            )
        )

    # Недопустимые символы в секрете -> ошибка
    with pytest.raises(ValueError, match="MAX_WEBHOOK_SECRET"):
        validate_max_setup_settings(
            Settings(
                max_bot_token="real-max-token",
                public_base_url="https://chistodvor.ru",
                max_webhook_secret="bad secret with spaces!",
            )
        )

    # Корректные боевые настройки -> (https://chistodvor.ru/webhooks/max, secret)
    url, secret = validate_max_setup_settings(
        Settings(
            max_bot_token="real-max-token",
            public_base_url="https://chistodvor.ru/",
            max_webhook_secret="chistodvor_max_webhook_2026_abc123",
        )
    )
    assert url == "https://chistodvor.ru/webhooks/max"
    assert secret == "chistodvor_max_webhook_2026_abc123"


@pytest.mark.asyncio
async def test_max_answer_callback_always_sends_notification() -> None:
    """MAX API POST /answers требует message или notification — пустой {} запрещён."""
    import json

    from src.services.messenger.max_client import MaxApiClient

    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        captured.append(body)
        if not body.get("notification") and not body.get("message"):
            return httpx.Response(
                400,
                json={
                    "code": "proto.payload",
                    "message": "Invalid request. message or notification required",
                },
            )
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(
        base_url="https://platform-api2.max.ru", transport=httpx.MockTransport(handler)
    ) as mock_http:
        max_client = MaxApiClient(token="test-token", http_client=mock_http)
        ok = await max_client.answer_callback("cb-123", target_chat_id=617884255)
        assert ok is True
        assert captured == [{"notification": "✓"}]


