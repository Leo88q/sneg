"""Доменные перечисления (строго синхронизированы с docs/02_database_schema.sql)."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    CLIENT = "client"
    EXECUTOR = "executor"
    FOREMAN = "foreman"
    ADMIN = "admin"
    OWNER = "owner"


class ExecutorStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BLOCKED = "blocked"


class AdminRole(StrEnum):
    FOREMAN = "foreman"
    ADMIN = "admin"
    OWNER = "owner"


class ConsentType(StrEnum):
    PDN = "pdn"
    CLIENT_OFFER = "client_offer"
    EXECUTOR_OFFER = "executor_offer"
    PHONE_TRANSFER = "phone_transfer"
    SAFETY_BRIEFING = "safety_briefing"


class ChatPurpose(StrEnum):
    DISPATCH_POOL = "dispatch_pool"
    FOREMAN_ALERTS = "foreman_alerts"
    OTHER = "other"


class GeoZoneTier(StrEnum):
    CORE = "core"
    EXTENDED = "extended"
    PLANNED = "planned"


class ServiceType(StrEnum):
    SNOW_YARD = "snow_yard"
    SNOW_ROAD = "snow_road"
    CAR_RESCUE = "car_rescue"
    YARD_HELP = "yard_help"
    MOVERS = "movers"
    ROOF_CLEANING = "roof_cleaning"
    REAGENTS = "reagents"


class CleaningMethod(StrEnum):
    MANUAL = "manual"
    SNOWBLOWER_OR_MINILOADER = "snowblower_or_miniloader"
    TRACTOR = "tractor"


class DepthBucket(StrEnum):
    UP_TO_10 = "up_to_10"
    FROM_10_TO_30 = "from_10_to_30"
    OVER_30 = "over_30"


class AccessConstraint(StrEnum):
    NARROW_GATE = "narrow_gate"
    STANDARD = "standard"
    WIDE_ROAD = "wide_road"


class UrgencyLevel(StrEnum):
    TODAY = "today"
    WITHIN_3H = "within_3h"
    NOW_OR_NIGHT = "now_or_night"


class ContactMode(StrEnum):
    BOT_PROXY = "bot_proxy"
    DIRECT_PHONE = "direct_phone"


class OrderStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_ADVANCE = "awaiting_advance"
    SEARCHING_EXECUTOR = "searching_executor"
    PARTIALLY_ASSIGNED = "partially_assigned"
    ASSIGNED = "assigned"
    EN_ROUTE = "en_route"
    ARRIVED = "arrived"
    PRICE_ADJUSTMENT_PENDING = "price_adjustment_pending"
    IN_PROGRESS = "in_progress"
    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    AWAITING_FINAL_PAYMENT = "awaiting_final_payment"
    COMPLETED = "completed"
    DISPUTED = "disputed"
    CANCELED_TIMEOUT = "canceled_timeout"
    CANCELED_BY_CLIENT = "canceled_by_client"
    CANCELED_BY_SERVICE = "canceled_by_service"


class OrderPaymentStatus(StrEnum):
    UNPAID = "unpaid"
    ADVANCE_PENDING = "advance_pending"
    ADVANCE_PAID = "advance_paid"
    FINAL_PENDING = "final_pending"
    FULLY_PAID = "fully_paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"


class AssignmentRole(StrEnum):
    PRIMARY = "primary"
    HELPER = "helper"


class AssignmentStatus(StrEnum):
    ASSIGNED = "assigned"
    EN_ROUTE = "en_route"
    ARRIVED = "arrived"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    RELEASED_BY_EXECUTOR = "released_by_executor"
    REMOVED_BY_FOREMAN = "removed_by_foreman"


class MediaStage(StrEnum):
    CLIENT_PROBLEM = "client_problem"
    BEFORE = "before"
    AFTER = "after"
    PRICE_ADJUSTMENT = "price_adjustment"
    DISPUTE = "dispute"


class PriceAdjustmentStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PaymentPurpose(StrEnum):
    ADVANCE = "advance"
    FINAL = "final"
    FULL_PREPAYMENT = "full_prepayment"
    SUBSCRIPTION = "subscription"


class PaymentProviderType(StrEnum):
    YOOKASSA = "yookassa"
    MOCK = "mock"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


class DisputeStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED_REWORK = "resolved_rework"
    RESOLVED_DISCOUNT = "resolved_discount"
    RESOLVED_REFUND = "resolved_refund"
    REJECTED = "rejected"


class LedgerEntryType(StrEnum):
    ORDER_EARNING = "order_earning"
    DISPATCH_COMPENSATION = "dispatch_compensation"
    URGENCY_BONUS = "urgency_bonus"
    PENALTY = "penalty"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class LedgerEntryStatus(StrEnum):
    ACCRUED = "accrued"
    INCLUDED_IN_BATCH = "included_in_batch"
    PAID = "paid"
    VOIDED = "voided"


class PayoutBatchStatus(StrEnum):
    DRAFT = "draft"
    PAID = "paid"


class SubscriptionPlanType(StrEnum):
    MONTHLY = "monthly"
    SEASON = "season"


class SubscriptionStatus(StrEnum):
    PENDING_PAYMENT = "pending_payment"
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    CANCELED = "canceled"


SERVICE_LABELS: dict[ServiceType, str] = {
    ServiceType.SNOW_YARD: "Уборка снега во дворе",
    ServiceType.SNOW_ROAD: "Расчистка дороги и проезда",
    ServiceType.CAR_RESCUE: "Срочно застрял",
    ServiceType.YARD_HELP: "Помощь по двору",
    ServiceType.MOVERS: "Грузчики на переезд",
    ServiceType.ROOF_CLEANING: "Расчистка крыши",
    ServiceType.REAGENTS: "Посыпка реагентами",
}

METHOD_LABELS: dict[CleaningMethod, str] = {
    CleaningMethod.MANUAL: "Вручную",
    CleaningMethod.SNOWBLOWER_OR_MINILOADER: "Снегоуборщик / мини-погрузчик",
    CleaningMethod.TRACTOR: "Трактор",
}

STATUS_LABELS: dict[OrderStatus, str] = {
    OrderStatus.DRAFT: "Черновик",
    OrderStatus.AWAITING_ADVANCE: "Ожидает аванс",
    OrderStatus.SEARCHING_EXECUTOR: "Поиск исполнителя",
    OrderStatus.PARTIALLY_ASSIGNED: "Частично назначен",
    OrderStatus.ASSIGNED: "Исполнитель назначен",
    OrderStatus.EN_ROUTE: "Исполнитель в пути",
    OrderStatus.ARRIVED: "Исполнитель на месте",
    OrderStatus.PRICE_ADJUSTMENT_PENDING: "Согласование цены",
    OrderStatus.IN_PROGRESS: "В работе",
    OrderStatus.AWAITING_ACCEPTANCE: "Ожидает приёмки",
    OrderStatus.AWAITING_FINAL_PAYMENT: "Ожидает доплаты",
    OrderStatus.COMPLETED: "Завершён",
    OrderStatus.DISPUTED: "Спор",
    OrderStatus.CANCELED_TIMEOUT: "Отменён (таймаут)",
    OrderStatus.CANCELED_BY_CLIENT: "Отменён клиентом",
    OrderStatus.CANCELED_BY_SERVICE: "Отменён сервисом",
}
