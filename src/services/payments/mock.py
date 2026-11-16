"""Mock-провайдер оплаты СБП для локальной отладки и интерактивного симулятора."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from src.core.exceptions import NotFoundError
from src.services.payments.base import (
    CreatePaymentCommand,
    CreateRefundCommand,
    PaymentResult,
    PaymentStatusResult,
    RefundResult,
    WebhookPaymentEvent,
)


class MockPaymentProvider:
    provider_name = "mock"

    def __init__(self, *, public_base_url: str = "") -> None:
        self.public_base_url = public_base_url.rstrip("/")
        self._store: dict[str, dict[str, Any]] = {}

    async def create_payment(self, cmd: CreatePaymentCommand) -> PaymentResult:
        pid = f"mock_pay_{uuid.uuid4().hex[:14]}"
        path = f"/mock-pay/{cmd.internal_payment_id}"
        url = f"{self.public_base_url}{path}" if self.public_base_url else path
        record = {
            "id": pid,
            "internal_id": str(cmd.internal_payment_id),
            "status": "pending",
            "paid": False,
            "amount": {"value": f"{cmd.amount:.2f}", "currency": "RUB"},
            "payment_method": {"type": "sbp"},
            "description": cmd.description,
            "confirmation_url": url,
            "receipt_items_count": len(cmd.receipt_items),
        }
        self._store[pid] = record
        return PaymentResult(
            provider=self.provider_name,
            provider_payment_id=pid,
            status="pending",
            confirmation_url=url,
            raw_response=record,
        )

    def mark_status(self, provider_payment_id: str, status: str) -> None:
        rec = self._store.get(provider_payment_id)
        if rec is not None:
            rec["status"] = status
            rec["paid"] = status == "succeeded"

    async def get_payment(self, provider_payment_id: str) -> PaymentStatusResult:
        rec = self._store.get(provider_payment_id)
        if rec is None:
            # После рестарта процесса возвращаем fallback (источник истины — БД)
            raise NotFoundError(f"Mock payment {provider_payment_id} not in memory")
        return PaymentStatusResult(
            provider_payment_id=provider_payment_id,
            status=str(rec["status"]),
            amount=Decimal(str(rec["amount"]["value"])),
            currency="RUB",
            paid=bool(rec["paid"]),
            raw_response=rec,
        )

    async def create_refund(self, cmd: CreateRefundCommand) -> RefundResult:
        rid = f"mock_ref_{uuid.uuid4().hex[:12]}"
        return RefundResult(
            provider_refund_id=rid,
            status="succeeded",
            raw_response={
                "id": rid,
                "payment_id": cmd.provider_payment_id,
                "amount": {"value": f"{cmd.amount:.2f}", "currency": "RUB"},
                "status": "succeeded",
                "reason": cmd.reason,
            },
        )

    def verify_webhook_ip(self, client_ip: str) -> bool:
        return True

    def parse_webhook(self, body: dict[str, Any]) -> WebhookPaymentEvent:
        obj = body.get("object", {})
        return WebhookPaymentEvent(
            event_type=str(body.get("event", "payment.succeeded")),
            provider_object_id=str(obj.get("id", "")),
            raw_body=body,
        )

    async def close(self) -> None:
        return None
