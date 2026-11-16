"""Адаптер ЮKassa API v3 (СБП capture=true, чеки 54-ФЗ, IP allowlist + GET verify)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from src.core.exceptions import ExternalIntegrationError, PaymentVerificationError
from src.core.security import is_ip_in_cidrs
from src.services.payments.base import (
    CreatePaymentCommand,
    CreateRefundCommand,
    PaymentResult,
    PaymentStatusResult,
    RefundResult,
    WebhookPaymentEvent,
)


class YooKassaProvider:
    provider_name = "yookassa"
    BASE_URL = "https://api.yookassa.ru/v3"

    def __init__(
        self,
        *,
        shop_id: str,
        secret_key: str,
        force_sbp: bool = True,
        tax_system_code: int = 2,
        vat_code: int = 1,
        trusted_cidrs: list[str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.shop_id = shop_id
        self.secret_key = secret_key
        self.force_sbp = force_sbp
        self.tax_system_code = tax_system_code
        self.vat_code = vat_code
        self.trusted_cidrs = trusted_cidrs or [
            "185.71.76.0/27",
            "185.71.77.0/27",
            "77.75.153.0/25",
            "77.75.156.11/32",
            "77.75.156.35/32",
            "77.75.154.128/25",
            "2a02:5180::/32",
        ]
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.BASE_URL,
            auth=(shop_id, secret_key),
            timeout=httpx.Timeout(20.0, connect=10.0),
        )

    def _build_receipt(self, cmd: CreatePaymentCommand) -> dict[str, Any] | None:
        if not cmd.receipt_items:
            return None
        customer: dict[str, str] = {}
        if cmd.customer_email:
            customer["email"] = cmd.customer_email
        if cmd.customer_phone:
            digits = "".join(ch for ch in cmd.customer_phone if ch.isdigit())
            if digits:
                customer["phone"] = digits
        if not customer:
            customer["email"] = "receipts@sneg-dispatcher.local"

        items = [
            {
                "description": item.description[:128],
                "quantity": f"{item.quantity:.3f}",
                "amount": {"value": f"{item.amount:.2f}", "currency": "RUB"},
                "vat_code": self.vat_code,
                "payment_mode": item.payment_mode,
                "payment_subject": item.payment_subject,
            }
            for item in cmd.receipt_items
        ]
        return {
            "customer": customer,
            "tax_system_code": self.tax_system_code,
            "items": items,
            "internet": True,
        }

    async def create_payment(self, cmd: CreatePaymentCommand) -> PaymentResult:
        payload: dict[str, Any] = {
            "amount": {"value": f"{cmd.amount:.2f}", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": cmd.return_url},
            # Для СБП в ЮKassa двухстадийное холдирование недоступно — строго capture=True
            "capture": True,
            "description": cmd.description[:128],
            "metadata": cmd.metadata,
        }
        if self.force_sbp:
            payload["payment_method_data"] = {"type": "sbp"}

        receipt = self._build_receipt(cmd)
        if receipt is not None:
            payload["receipt"] = receipt

        resp = await self._client.post(
            "/payments",
            headers={"Idempotence-Key": str(cmd.idempotency_key)},
            json=payload,
        )
        if resp.status_code not in (200, 201):
            raise ExternalIntegrationError(
                f"YooKassa create_payment error {resp.status_code}: {resp.text}"
            )
        data = resp.json()
        confirmation_url = str(data.get("confirmation", {}).get("confirmation_url", ""))
        return PaymentResult(
            provider=self.provider_name,
            provider_payment_id=str(data["id"]),
            status=str(data.get("status", "pending")),
            confirmation_url=confirmation_url,
            raw_response=data,
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentStatusResult:
        resp = await self._client.get(f"/payments/{provider_payment_id}")
        if resp.status_code != 200:
            raise ExternalIntegrationError(
                f"YooKassa get_payment error {resp.status_code}: {resp.text}"
            )
        data = resp.json()
        amount_obj = data.get("amount", {})
        return PaymentStatusResult(
            provider_payment_id=str(data["id"]),
            status=str(data.get("status", "pending")),
            amount=Decimal(str(amount_obj.get("value", "0.00"))),
            currency=str(amount_obj.get("currency", "RUB")),
            paid=bool(data.get("paid", False)),
            raw_response=data,
        )

    async def create_refund(self, cmd: CreateRefundCommand) -> RefundResult:
        resp = await self._client.post(
            "/refunds",
            headers={"Idempotence-Key": str(cmd.idempotency_key)},
            json={
                "payment_id": cmd.provider_payment_id,
                "amount": {"value": f"{cmd.amount:.2f}", "currency": "RUB"},
                "description": cmd.reason[:250],
            },
        )
        if resp.status_code not in (200, 201):
            raise ExternalIntegrationError(
                f"YooKassa create_refund error {resp.status_code}: {resp.text}"
            )
        data = resp.json()
        return RefundResult(
            provider_refund_id=str(data["id"]),
            status=str(data.get("status", "pending")),
            raw_response=data,
        )

    def verify_webhook_ip(self, client_ip: str) -> bool:
        return is_ip_in_cidrs(client_ip, self.trusted_cidrs)

    def parse_webhook(self, body: dict[str, Any]) -> WebhookPaymentEvent:
        event_type = body.get("event")
        obj = body.get("object")
        if not event_type or not isinstance(obj, dict) or not obj.get("id"):
            raise PaymentVerificationError("Некорректный формат уведомления ЮKassa")
        return WebhookPaymentEvent(
            event_type=str(event_type),
            provider_object_id=str(obj["id"]),
            raw_body=body,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
