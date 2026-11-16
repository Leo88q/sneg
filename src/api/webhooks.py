"""Вебхуки MAX Bot API (/webhooks/max) и ЮKassa (/webhooks/yookassa)."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.api.deps import get_container
from src.core.container import AppContainer
from src.core.exceptions import PaymentVerificationError, UnauthorizedError
from src.core.security import verify_webhook_secret
from src.infra.db.models import Payment, ProcessedWebhookEvent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/max")
async def max_webhook(
    request: Request,
    x_max_bot_api_secret: str | None = Header(default=None, alias="X-Max-Bot-Api-Secret"),
    container: AppContainer = Depends(get_container),
) -> dict[str, bool]:
    if not verify_webhook_secret(container.settings.max_webhook_secret, x_max_bot_api_secret):
        raise UnauthorizedError("Неверный заголовок X-Max-Bot-Api-Secret")

    body: dict[str, Any] = await request.json()
    await container.bot_router.handle_update(body)
    return {"ok": True}


@router.post("/yookassa")
async def yookassa_webhook(
    request: Request,
    container: AppContainer = Depends(get_container),
) -> Response:
    client_ip = (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For")
        or (request.client.host if request.client else "")
    )
    if not container.payment_provider.verify_webhook_ip(client_ip):
        logger.warning("Rejected YooKassa webhook from untrusted IP: %s", client_ip)
        raise PaymentVerificationError(f"IP {client_ip} не входит в доверенный список ЮKassa")

    body: dict[str, Any] = await request.json()
    event = container.payment_provider.parse_webhook(body)
    event_key = f"{event.event_type}:{event.provider_object_id}"

    # Идемпотентность входящего вебхука
    async with container.session_factory() as session:
        session.add(ProcessedWebhookEvent(source="yookassa", event_key=event_key))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return Response(status_code=200)

    if event.event_type in ("payment.succeeded", "payment.canceled"):
        # Обязательная обратная перепроверка статуса через GET /v3/payments/{id}
        verified = await container.payment_provider.get_payment(event.provider_object_id)

        async with container.session_factory() as session:
            payment = (
                await session.execute(
                    select(Payment).where(
                        Payment.provider_payment_id == event.provider_object_id
                    )
                )
            ).scalar_one_or_none()
        if payment is None:
            logger.warning("Payment %s not found in DB", event.provider_object_id)
            return Response(status_code=200)

        if verified.currency != payment.currency or verified.amount != Decimal(payment.amount):
            raise PaymentVerificationError("Несовпадение суммы или валюты платежа при проверке ЮKassa")

        if verified.status == "succeeded" and verified.paid:
            await container.orchestrator.confirm_payment_succeeded(payment.id)
        elif verified.status == "canceled":
            await container.orchestrator.cancel_payment(payment.id)

    return Response(status_code=200)
