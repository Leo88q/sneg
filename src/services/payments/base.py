"""Протокол PaymentProvider (ТЗ B8.1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol


@dataclass(slots=True)
class ReceiptItem:
    description: str
    amount: Decimal
    quantity: Decimal = Decimal("1.000")
    payment_mode: Literal["partial_prepayment", "full_prepayment", "full_payment"] = (
        "partial_prepayment"
    )
    payment_subject: str = "service"


@dataclass(slots=True)
class CreatePaymentCommand:
    internal_payment_id: uuid.UUID
    idempotency_key: uuid.UUID
    amount: Decimal
    description: str
    return_url: str
    metadata: dict[str, str] = field(default_factory=dict)
    customer_email: str | None = None
    customer_phone: str | None = None
    receipt_items: list[ReceiptItem] = field(default_factory=list)
    prepayment_offset: Decimal = Decimal("0.00")


@dataclass(slots=True)
class PaymentResult:
    provider: str
    provider_payment_id: str
    status: str
    confirmation_url: str
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PaymentStatusResult:
    provider_payment_id: str
    status: str  # pending | succeeded | canceled
    amount: Decimal
    currency: str = "RUB"
    paid: bool = False
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CreateRefundCommand:
    provider_payment_id: str
    idempotency_key: uuid.UUID
    amount: Decimal
    reason: str


@dataclass(slots=True)
class RefundResult:
    provider_refund_id: str
    status: str  # pending | succeeded | canceled
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookPaymentEvent:
    event_type: str  # payment.succeeded | payment.canceled | refund.succeeded
    provider_object_id: str
    raw_body: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    provider_name: str

    async def create_payment(self, cmd: CreatePaymentCommand) -> PaymentResult: ...

    async def get_payment(self, provider_payment_id: str) -> PaymentStatusResult: ...

    async def create_refund(self, cmd: CreateRefundCommand) -> RefundResult: ...

    def verify_webhook_ip(self, client_ip: str) -> bool: ...

    def parse_webhook(self, body: dict[str, Any]) -> WebhookPaymentEvent: ...

    async def close(self) -> None: ...
