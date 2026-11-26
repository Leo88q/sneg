"""REST API веб-админ-панели (/api/v1/admin/*) и авторизованная выдача медиа."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.api.deps import get_container, get_current_admin, require_roles, write_audit
from src.core.container import AppContainer
from src.core.enums import (
    SERVICE_LABELS,
    STATUS_LABELS,
    AccessConstraint,
    AdminRole,
    CleaningMethod,
    DepthBucket,
    DisputeStatus,
    ExecutorStatus,
    LedgerEntryStatus,
    LedgerEntryType,
    OrderStatus,
    ServiceType,
    UrgencyLevel,
)
from src.core.exceptions import NotFoundError, UnauthorizedError, ValidationAppError
from src.core.security import create_admin_jwt, verify_password
from src.domain.pricing.calculator import ZERO, PriceCalculationInput, calculate_price, to_decimal
from src.domain.pricing.payment_plan import (
    FinalPaymentRoute,
    PaymentSchedule,
    estimate_payment_plan,
)
from src.infra.db.models import (
    AdminAccount,
    AuditLog,
    BotChat,
    DataSubjectRequest,
    Dispute,
    ExecutorProfile,
    GeoZone,
    Order,
    OrderAssignment,
    OrderMedia,
    OrderMessage,
    OrderStatusHistory,
    Payment,
    PayoutBatch,
    PayoutLedgerEntry,
    PriceAdjustment,
    Refund,
    Subscription,
    TariffConfig,
    User,
    UserConsent,
    utcnow,
)
from src.services.messenger.base import MessageTarget, OutgoingMessage

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
media_router = APIRouter(prefix="/api/v1/media", tags=["media"])


# ---------------------------------------------------------------------------
# Pydantic-схемы запросов
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class AssignExecutorRequest(BaseModel):
    executor_user_id: uuid.UUID


class UnassignExecutorRequest(BaseModel):
    executor_user_id: uuid.UUID
    reason: str = "Снято администратором"
    penalty_rub: Decimal = ZERO


class PriceAdjustmentRequest(BaseModel):
    new_total: Decimal
    reason: str


class CancelOrderRequest(BaseModel):
    reason: str = "Отменено диспетчером"
    by_service: bool = True


class RefundRequest(BaseModel):
    amount: Decimal
    reason: str


class TariffUpdateRequest(BaseModel):
    config_json: dict[str, Any]
    comment: str = ""


class TariffPreviewRequest(BaseModel):
    service_type: ServiceType
    geo_zone_code: str = "malakhovka"
    area_m2: Decimal | None = Decimal("120")
    length_m: Decimal | None = None
    width_m: Decimal | None = None
    depth_bucket: DepthBucket = DepthBucket.FROM_10_TO_30
    access_constraint: AccessConstraint = AccessConstraint.STANDARD
    cleaning_method: CleaningMethod | None = None
    urgency: UrgencyLevel = UrgencyLevel.WITHIN_3H
    snow_removal: bool = False
    vehicle_type: str = "sedan"
    distance_km: Decimal = Decimal("4")
    hours: int = 2
    workers_count: int = 1
    floors_without_lift: int = 0
    needs_equipment: bool = False
    force_night: bool | None = None
    group_discount_pct: Decimal = ZERO
    override_config: dict[str, Any] | None = None


class MarketplacePreviewRequest(BaseModel):
    total: Decimal
    schedule: PaymentSchedule
    final_payment_route: FinalPaymentRoute | None = None
    rental_estimate: Decimal | None = Field(
        default=None, ge=0, le=Decimal("9999999999.99"), decimal_places=2
    )


class GeoZoneCreateRequest(BaseModel):
    code: str
    name: str
    tier: str = "extended"
    is_active: bool = True
    dispatch_fee: Decimal = Decimal("600.00")
    base_lat: float = 55.6435
    base_lon: float = 38.0068
    free_radius_km: Decimal = Decimal("5.00")
    per_km_rate: Decimal = Decimal("100.00")


class GeoZonePatchRequest(BaseModel):
    name: str | None = None
    tier: str | None = None
    is_active: bool | None = None
    dispatch_fee: Decimal | None = None
    free_radius_km: Decimal | None = None
    per_km_rate: Decimal | None = None


class ExecutorVerifyRequest(BaseModel):
    status: ExecutorStatus
    notes: str | None = None


class ExecutorPermissionsRequest(BaseModel):
    share_pct_override: Decimal | None = None
    max_active_orders: int | None = Field(default=None, ge=1, le=10)
    has_snowblower: bool | None = None
    has_miniloader: bool | None = None
    has_tractor: bool | None = None
    roof_certified: bool | None = None
    insurance_policy_no: str | None = None
    insurance_valid_until: date | None = None


class ExecutorPenaltyRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    reason: str
    order_id: uuid.UUID | None = None


class DisputeResolveRequest(BaseModel):
    resolution: DisputeStatus
    note: str
    discount_amount: Decimal = ZERO
    refund_amount: Decimal = ZERO


class CreatePayoutBatchRequest(BaseModel):
    executor_id: uuid.UUID


class MarkBatchPaidRequest(BaseModel):
    payment_method_note: str = "Перевод СБП самозанятому"
    npd_receipt_id: str | None = None
    npd_receipt_url: str | None = None


class BotChatPatchRequest(BaseModel):
    chat_purpose: str | None = None
    is_active: bool | None = None
    title: str | None = None


class BroadcastRequest(BaseModel):
    target_role: str = "client"  # client | executor | all
    text: str


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@router.post("/auth/login")
async def admin_login(
    body: LoginRequest,
    response: Response,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        admin = (
            await session.execute(
                select(AdminAccount).where(AdminAccount.email == body.email.strip().lower())
            )
        ).scalar_one_or_none()
        if admin is None or not admin.is_active or not verify_password(body.password, admin.password_hash):
            raise UnauthorizedError("Неверный email или пароль")
        admin.last_login_at = utcnow()
        await session.commit()

    token = create_admin_jwt(
        admin_id=str(admin.id),
        email=admin.email,
        role=admin.role,
        secret=container.settings.admin_jwt_secret,
        ttl_minutes=container.settings.admin_jwt_ttl_minutes,
    )
    response.set_cookie(
        key="sneg_admin_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=(container.settings.app_env == "production"),
        max_age=container.settings.admin_jwt_ttl_minutes * 60,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "admin": {
            "id": str(admin.id),
            "email": admin.email,
            "full_name": admin.full_name,
            "role": admin.role,
        },
    }


@router.post("/auth/logout")
async def admin_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie("sneg_admin_token")
    return {"ok": True}


@router.get("/auth/me")
async def admin_me(admin: AdminAccount = Depends(get_current_admin)) -> dict[str, Any]:
    return {
        "id": str(admin.id),
        "email": admin.email,
        "full_name": admin.full_name,
        "role": admin.role,
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard/summary")
async def dashboard_summary(
    _admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        orders = list((await session.execute(select(Order))).scalars().all())
        disputes_open = (
            await session.execute(
                select(func.count(Dispute.id)).where(
                    Dispute.status.in_([DisputeStatus.OPEN.value, DisputeStatus.IN_REVIEW.value])
                )
            )
        ).scalar() or 0
        pending_execs = (
            await session.execute(
                select(func.count(ExecutorProfile.user_id)).where(
                    ExecutorProfile.status == ExecutorStatus.PENDING_VERIFICATION.value
                )
            )
        ).scalar() or 0
        active_subs = (
            await session.execute(
                select(func.count(Subscription.id)).where(Subscription.status == "active")
            )
        ).scalar() or 0
        unpaid_payouts = (
            await session.execute(
                select(func.coalesce(func.sum(PayoutLedgerEntry.amount), 0)).where(
                    PayoutLedgerEntry.status == LedgerEntryStatus.ACCRUED.value
                )
            )
        ).scalar() or ZERO

    by_status: dict[str, int] = {}
    gmv = ZERO
    paid_total = ZERO
    attention_orders: list[dict[str, Any]] = []
    for o in orders:
        by_status[o.status] = by_status.get(o.status, 0) + 1
        if o.status not in (
            OrderStatus.CANCELED_BY_CLIENT.value,
            OrderStatus.CANCELED_BY_SERVICE.value,
            OrderStatus.CANCELED_TIMEOUT.value,
        ):
            gmv += o.final_total
        paid_total += o.paid_amount
        if o.status in (
            OrderStatus.SEARCHING_EXECUTOR.value,
            OrderStatus.DISPUTED.value,
            OrderStatus.PRICE_ADJUSTMENT_PENDING.value,
        ):
            attention_orders.append(
                {
                    "id": str(o.id),
                    "public_number": o.public_number,
                    "service_type": o.service_type,
                    "status": o.status,
                    "status_label": STATUS_LABELS.get(OrderStatus(o.status), o.status),
                    "public_area_hint": o.public_area_hint,
                    "final_total": str(o.final_total),
                    "created_at": o.created_at.isoformat(),
                }
            )

    total_orders = len(orders)
    dispute_share = round((disputes_open / total_orders * 100), 2) if total_orders else 0.0

    return {
        "total_orders": total_orders,
        "by_status": by_status,
        "gmv_rub": str(gmv.quantize(Decimal("0.01"))),
        "paid_rub": str(paid_total.quantize(Decimal("0.01"))),
        "accrued_payouts_rub": str(Decimal(unpaid_payouts).quantize(Decimal("0.01"))),
        "open_disputes": int(disputes_open),
        "dispute_share_pct": dispute_share,
        "pending_executors": int(pending_execs),
        "active_subscriptions": int(active_subs),
        "attention_orders": attention_orders[:15],
    }


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@router.get("/orders")
async def list_orders(
    status: str | None = None,
    service_type: str | None = None,
    q: str | None = None,
    _admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        stmt = select(Order).order_by(Order.created_at.desc()).limit(200)
        if status:
            stmt = stmt.where(Order.status == status)
        if service_type:
            stmt = stmt.where(Order.service_type == service_type)
        orders = list((await session.execute(stmt)).scalars().all())
        users = {
            u.id: u
            for u in (await session.execute(select(User))).scalars().all()
        }
        zones = {
            z.id: z
            for z in (await session.execute(select(GeoZone))).scalars().all()
        }

    result: list[dict[str, Any]] = []
    q_low = q.lower().strip() if q else ""
    for o in orders:
        client = users.get(o.client_id)
        zone = zones.get(o.geo_zone_id) if o.geo_zone_id else None
        client_phone = (
            (client.shared_contact_phone or client.bot_phone or "") if client else ""
        )
        if q_low and (
            q_low not in str(o.public_number)
            and q_low not in o.address_text.lower()
            and q_low not in (client.display_name.lower() if client else "")
            and q_low not in client_phone.lower()
        ):
            continue
        source = str((o.params_json or {}).get("source", "max_bot"))
        result.append(
            {
                "id": str(o.id),
                "public_number": o.public_number,
                "service_type": o.service_type,
                "service_label": SERVICE_LABELS.get(ServiceType(o.service_type), o.service_type),
                "status": o.status,
                "status_label": STATUS_LABELS.get(OrderStatus(o.status), o.status),
                "payment_status": o.payment_status,
                "zone_name": zone.name if zone else "",
                "address_text": o.address_text,
                "public_area_hint": o.public_area_hint,
                "client_name": client.display_name if client else "",
                "client_phone": client_phone,
                "source": source,
                "estimated_total": str(o.estimated_total),
                "final_total": str(o.final_total),
                "advance_amount": str(o.advance_amount),
                "paid_amount": str(o.paid_amount),
                "required_executors": o.required_executors,
                "client_rating": o.client_rating,
                "created_at": o.created_at.isoformat(),
            }
        )
    return result


@router.get("/orders/{order_id}")
async def get_order_detail(
    order_id: uuid.UUID,
    _admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        order = await session.get(Order, order_id)
        if order is None:
            raise NotFoundError("Заказ не найден")
        client = await session.get(User, order.client_id)
        zone = await session.get(GeoZone, order.geo_zone_id) if order.geo_zone_id else None
        assignments = list(
            (
                await session.execute(
                    select(OrderAssignment).where(OrderAssignment.order_id == order.id)
                )
            ).scalars().all()
        )
        exec_users = {
            a.executor_id: await session.get(User, a.executor_id) for a in assignments
        }
        media = list(
            (
                await session.execute(
                    select(OrderMedia)
                    .where(OrderMedia.order_id == order.id)
                    .order_by(OrderMedia.created_at.asc())
                )
            ).scalars().all()
        )
        history = list(
            (
                await session.execute(
                    select(OrderStatusHistory)
                    .where(OrderStatusHistory.order_id == order.id)
                    .order_by(OrderStatusHistory.created_at.asc())
                )
            ).scalars().all()
        )
        payments = list(
            (
                await session.execute(
                    select(Payment)
                    .where(Payment.order_id == order.id)
                    .order_by(Payment.created_at.asc())
                )
            ).scalars().all()
        )
        refunds = list(
            (
                await session.execute(
                    select(Refund).where(Refund.order_id == order.id)
                )
            ).scalars().all()
        )
        adjustments = list(
            (
                await session.execute(
                    select(PriceAdjustment).where(PriceAdjustment.order_id == order.id)
                )
            ).scalars().all()
        )
        messages = list(
            (
                await session.execute(
                    select(OrderMessage)
                    .where(OrderMessage.order_id == order.id)
                    .order_by(OrderMessage.created_at.asc())
                )
            ).scalars().all()
        )
        disputes = list(
            (
                await session.execute(select(Dispute).where(Dispute.order_id == order.id))
            ).scalars().all()
        )

    return {
        "id": str(order.id),
        "public_number": order.public_number,
        "service_type": order.service_type,
        "service_label": SERVICE_LABELS.get(ServiceType(order.service_type), order.service_type),
        "status": order.status,
        "status_label": STATUS_LABELS.get(OrderStatus(order.status), order.status),
        "payment_status": order.payment_status,
        "zone_name": zone.name if zone else "",
        "address_text": order.address_text,
        "public_area_hint": order.public_area_hint,
        "lat": order.lat,
        "lon": order.lon,
        "params_json": order.params_json,
        "cleaning_method": order.cleaning_method,
        "urgency": order.urgency,
        "required_executors": order.required_executors,
        "pricing_snapshot": order.pricing_snapshot,
        "estimated_total": str(order.estimated_total),
        "final_total": str(order.final_total),
        "advance_amount": str(order.advance_amount),
        "paid_amount": str(order.paid_amount),
        "group_discount_amount": str(order.group_discount_amount),
        "executor_bonus_amount": str(order.executor_bonus_amount),
        "contact_mode": order.contact_mode,
        "client_comment": order.client_comment,
        "source": str((order.params_json or {}).get("source", "max_bot")),
        "client": {
            "id": str(client.id) if client else "",
            "display_name": client.display_name if client else "",
            "max_user_id": client.max_user_id if client else None,
            "shared_contact_phone": client.shared_contact_phone if client else None,
            "email": client.email if client else None,
        },
        "assignments": [
            {
                "id": str(a.id),
                "executor_id": str(a.executor_id),
                "executor_name": (
                    u.display_name if (u := exec_users.get(a.executor_id)) is not None else ""
                ),
                "role_in_order": a.role_in_order,
                "status": a.status,
                "executor_share_pct": str(a.executor_share_pct),
                "accepted_at": a.accepted_at.isoformat(),
            }
            for a in assignments
        ],
        "media": [
            {
                "id": str(m.id),
                "stage": m.stage,
                "mime_type": m.mime_type,
                "size_bytes": m.size_bytes,
                "url": f"/api/v1/media/file/{m.id}",
                "created_at": m.created_at.isoformat(),
            }
            for m in media
        ],
        "history": [
            {
                "from_status": h.from_status,
                "to_status": h.to_status,
                "reason": h.reason,
                "created_at": h.created_at.isoformat(),
            }
            for h in history
        ],
        "payments": [
            {
                "id": str(p.id),
                "purpose": p.purpose,
                "provider": p.provider,
                "provider_payment_id": p.provider_payment_id,
                "amount": str(p.amount),
                "status": p.status,
                "confirmation_url": p.confirmation_url,
                "receipt_status": p.receipt_status,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            }
            for p in payments
        ],
        "refunds": [
            {
                "id": str(r.id),
                "amount": str(r.amount),
                "status": r.status,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in refunds
        ],
        "price_adjustments": [
            {
                "id": str(adj.id),
                "old_total": str(adj.old_total),
                "new_total": str(adj.new_total),
                "delta_pct": str(adj.delta_pct),
                "reason": adj.reason,
                "status": adj.status,
                "created_at": adj.created_at.isoformat(),
            }
            for adj in adjustments
        ],
        "messages": [
            {
                "id": str(m.id),
                "sender_user_id": str(m.sender_user_id),
                "text": m.text,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
        "disputes": [
            {
                "id": str(d.id),
                "reason": d.reason,
                "status": d.status,
                "resolution_note": d.resolution_note,
                "discount_amount": str(d.discount_amount),
                "refund_amount": str(d.refund_amount),
            }
            for d in disputes
        ],
    }


@router.post("/orders/{order_id}/dispatch")
async def admin_dispatch_order(
    order_id: uuid.UUID,
    request: Request,
    mark_advance_paid: bool = False,
    admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    order = await container.orchestrator.publish_order_to_dispatch(
        order_id=order_id,
        actor_label=f"Админка ({admin.email})",
        actor_admin_id=admin.id,
        mark_advance_paid=mark_advance_paid,
    )
    async with container.session_factory() as session:
        await write_audit(
            session,
            admin=admin,
            action="order.dispatch_to_chat",
            entity_type="order",
            entity_id=str(order_id),
            diff={
                "status": order.status,
                "dispatch_chat_id": order.dispatch_chat_id,
                "mark_advance_paid": mark_advance_paid,
            },
            ip=request.client.host if request.client else None,
        )
        await session.commit()
    return {
        "order_status": order.status,
        "dispatch_chat_id": order.dispatch_chat_id,
        "dispatch_message_id": order.dispatch_message_id,
    }


@router.post("/orders/{order_id}/assign")
async def admin_assign_order(
    order_id: uuid.UUID,
    body: AssignExecutorRequest,
    request: Request,
    admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    order, asgn = await container.orchestrator.take_order_atomic(
        order_id=order_id,
        executor_user_id=body.executor_user_id,
        assigned_by_admin_id=admin.id,
    )
    async with container.session_factory() as session:
        await write_audit(
            session,
            admin=admin,
            action="order.assign",
            entity_type="order",
            entity_id=str(order_id),
            diff={"executor_user_id": str(body.executor_user_id)},
            ip=request.client.host if request.client else None,
        )
        await session.commit()
    return {"order_status": order.status, "assignment_id": str(asgn.id)}


@router.post("/orders/{order_id}/unassign")
async def admin_unassign_order(
    order_id: uuid.UUID,
    body: UnassignExecutorRequest,
    request: Request,
    admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    order = await container.orchestrator.unassign_executor(
        order_id=order_id,
        executor_user_id=body.executor_user_id,
        reason=body.reason,
        by_foreman=True,
        actor_admin_id=admin.id,
        penalty_rub=body.penalty_rub,
    )
    async with container.session_factory() as session:
        await write_audit(
            session,
            admin=admin,
            action="order.unassign",
            entity_type="order",
            entity_id=str(order_id),
            diff={"executor_user_id": str(body.executor_user_id), "penalty": str(body.penalty_rub)},
            ip=request.client.host if request.client else None,
        )
        await session.commit()
    return {"order_status": order.status}


@router.post("/orders/{order_id}/price-adjustment")
async def admin_price_adjustment(
    order_id: uuid.UUID,
    body: PriceAdjustmentRequest,
    request: Request,
    admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    proposer_uid = admin.linked_user_id
    if proposer_uid is None:
        async with container.session_factory() as session:
            u = (await session.execute(select(User).limit(1))).scalar_one_or_none()
            if u is None:
                raise ValidationAppError("Не найден связанный пользователь для корректировки")
            proposer_uid = u.id
    adj = await container.orchestrator.propose_price_adjustment(
        order_id=order_id,
        proposer_user_id=proposer_uid,
        new_total=body.new_total,
        reason=body.reason,
        actor_admin_id=admin.id,
    )
    async with container.session_factory() as session:
        await write_audit(
            session,
            admin=admin,
            action="order.price_adjustment",
            entity_type="order",
            entity_id=str(order_id),
            diff={"old_total": str(adj.old_total), "new_total": str(adj.new_total)},
            ip=request.client.host if request.client else None,
        )
        await session.commit()
    return {"id": str(adj.id), "status": adj.status, "delta_pct": str(adj.delta_pct)}


@router.post("/orders/{order_id}/cancel")
async def admin_cancel_order(
    order_id: uuid.UUID,
    body: CancelOrderRequest,
    request: Request,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    order = await container.orchestrator.cancel_order(
        order_id=order_id,
        by_service=body.by_service,
        reason=body.reason,
        actor_admin_id=admin.id,
    )
    async with container.session_factory() as session:
        await write_audit(
            session,
            admin=admin,
            action="order.cancel",
            entity_type="order",
            entity_id=str(order_id),
            diff={"reason": body.reason, "by_service": body.by_service},
            ip=request.client.host if request.client else None,
        )
        await session.commit()
    return {"order_status": order.status, "payment_status": order.payment_status}


@router.post("/orders/{order_id}/refund")
async def admin_refund_order(
    order_id: uuid.UUID,
    body: RefundRequest,
    request: Request,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        order = await session.get(Order, order_id)
        if order is None:
            raise NotFoundError("Заказ не найден")
        ref = await container.orchestrator._execute_refund_in_session(
            session, order=order, amount=body.amount, reason=body.reason, admin_id=admin.id
        )
        if ref is None:
            raise ValidationAppError("Нет оплаченных платежей для возврата")
        await write_audit(
            session,
            admin=admin,
            action="order.refund",
            entity_type="order",
            entity_id=str(order_id),
            diff={"amount": str(ref.amount), "reason": body.reason},
            ip=request.client.host if request.client else None,
        )
        await session.commit()
        return {"refund_id": str(ref.id), "status": ref.status, "amount": str(ref.amount)}


# ---------------------------------------------------------------------------
# Tariffs & Calculator Sandbox
# ---------------------------------------------------------------------------


@router.get("/tariffs/current")
async def get_current_tariff(
    _admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        t = await container.orchestrator.get_active_tariff(session)
    return {
        "id": str(t.id),
        "version": t.version,
        "comment": t.comment,
        "config_json": t.config_json,
        "created_at": t.created_at.isoformat(),
    }


@router.get("/tariffs/history")
async def get_tariff_history(
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        items = list(
            (
                await session.execute(
                    select(TariffConfig).order_by(TariffConfig.version.desc()).limit(25)
                )
            ).scalars().all()
        )
    return [
        {
            "id": str(t.id),
            "version": t.version,
            "is_active": t.is_active,
            "comment": t.comment,
            "created_at": t.created_at.isoformat(),
        }
        for t in items
    ]


@router.put("/tariffs")
async def update_tariff(
    body: TariffUpdateRequest,
    request: Request,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    # Валидируем, что новый JSON рабочий в калькуляторе
    try:
        calculate_price(
            PriceCalculationInput(
                service_type=ServiceType.SNOW_YARD,
                area_m2=Decimal("100"),
                dispatch_fee=Decimal("500"),
            ),
            body.config_json,
        )
    except Exception as exc:
        raise ValidationAppError(f"Некорректная структура тарифа: {exc}") from exc

    async with container.session_factory() as session:
        current = await container.orchestrator.get_active_tariff(session)
        old_ver = current.version
        current.is_active = False
        await session.flush()
        new_tariff = TariffConfig(
            version=old_ver + 1,
            is_active=True,
            config_json=body.config_json,
            comment=body.comment or f"Обновление тарифа до v{old_ver + 1}",
            updated_by=admin.id,
        )
        session.add(new_tariff)
        await session.flush()
        await write_audit(
            session,
            admin=admin,
            action="tariff.update",
            entity_type="tariff_config",
            entity_id=str(new_tariff.id),
            diff={"old_version": old_ver, "new_version": new_tariff.version},
            ip=request.client.host if request.client else None,
        )
        await session.commit()
        await session.refresh(new_tariff)
    return {
        "id": str(new_tariff.id),
        "version": new_tariff.version,
        "comment": new_tariff.comment,
        "config_json": new_tariff.config_json,
    }


@router.post("/tariffs/preview")
async def preview_tariff_calculation(
    body: TariffPreviewRequest,
    _admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        tariff = await container.orchestrator.get_active_tariff(session)
        zone = (
            await session.execute(select(GeoZone).where(GeoZone.code == body.geo_zone_code))
        ).scalar_one_or_none()
    cfg = body.override_config or tariff.config_json
    bd = calculate_price(
        PriceCalculationInput(
            service_type=body.service_type,
            dispatch_fee=zone.dispatch_fee if zone else Decimal("500.00"),
            area_m2=body.area_m2,
            length_m=body.length_m,
            width_m=body.width_m,
            depth_bucket=body.depth_bucket,
            access_constraint=body.access_constraint,
            cleaning_method=body.cleaning_method,
            urgency=body.urgency,
            snow_removal=body.snow_removal,
            vehicle_type=body.vehicle_type,
            distance_km=body.distance_km,
            hours=body.hours,
            workers_count=body.workers_count,
            floors_without_lift=body.floors_without_lift,
            needs_equipment=body.needs_equipment,
            force_night=body.force_night,
            group_discount_pct=body.group_discount_pct,
        ),
        cfg,
    )
    return {
        "estimated_total": str(bd.estimated_total),
        "advance_amount": str(bd.advance_amount),
        "remainder_amount": str(bd.remainder_amount),
        "work_cost": str(bd.work_cost),
        "dispatch_fee": str(bd.dispatch_fee),
        "extras_cost": str(bd.extras_cost),
        "group_discount_amount": str(bd.group_discount_amount),
        "estimated_executor_payout": str(bd.estimated_executor_payout),
        "executor_share_pct": str(bd.executor_share_pct),
        "cleaning_method": bd.cleaning_method.value if bd.cleaning_method else None,
        "pricing_snapshot": bd.pricing_snapshot,
    }


@router.post("/marketplace/preview")
async def preview_marketplace(
    body: MarketplacePreviewRequest,
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
) -> dict[str, Any]:
    """Закрытый расчёт и макеты; никаких заказов, сообщений и платежей не создаёт."""
    plan = estimate_payment_plan(
        body.total, schedule=body.schedule, final_payment_route=body.final_payment_route
    )
    estimate = plan.estimate
    route_labels = {
        FinalPaymentRoute.PAYMENT_PROVIDER: "через платёжного партнёра",
        FinalPaymentRoute.DIRECT_TO_EXECUTOR: "напрямую исполнителю",
    }
    route = route_labels[plan.final_payment_route] if plan.final_payment_route else "остатка нет"
    timing_lines = (
        f"До работ: {plan.upfront:.2f} ₽\n"
        f"После работ: {plan.after_completion:.2f} ₽ ({route})"
    )
    payment_lines = f"Стоимость заказа: {estimate.total:.2f} ₽\n{timing_lines}"
    client_example = (
        "ПРОЕКТ · Пример для сайта\n"
        "ЧистоДвор помогает подобрать исполнителя и сопровождать заказ.\n"
        f"{payment_lines}\n"
        "Договор и условия оплаты требуют согласования."
    )
    bot_example = (
        "ПРОЕКТ · Пример сообщения клиенту в MAX\n"
        f"{payment_lines}\n"
        "Исполнитель и условия согласуются до заключения договора."
    )
    worker_example = (
        "ПРОЕКТ · Пример для группы исполнителей (один исполнитель)\n"
        f"Стоимость заказа: {estimate.total:.2f} ₽\n"
        f"Комиссия сервиса 27%: {estimate.commission:.2f} ₽\n"
        f"После комиссии: {estimate.executor_after_commission:.2f} ₽ "
        "до налогов и расходов, включая аренду техники.\n"
        f"{timing_lines}\n"
        "Расчёт не является предложением заказа или подтверждением выплаты."
    )
    return {
        "preview_only": True,
        "total": str(estimate.total),
        "commission_percent": str(estimate.commission_percent),
        "commission": str(estimate.commission),
        "executor_after_commission": str(estimate.executor_after_commission),
        "rental_estimate": str(body.rental_estimate) if body.rental_estimate is not None else None,
        "after_commission_and_rental": (
            str(estimate.executor_after_commission - body.rental_estimate)
            if body.rental_estimate is not None else None
        ),
        "schedule": plan.schedule.value,
        "upfront": str(plan.upfront),
        "after_completion": str(plan.after_completion),
        "final_payment_route": plan.final_payment_route.value if plan.final_payment_route else None,
        "site_example": client_example,
        "bot_example": bot_example,
        "group_example": worker_example,
    }


# ---------------------------------------------------------------------------
# GeoZones
# ---------------------------------------------------------------------------


@router.get("/geo-zones")
async def list_geo_zones(
    _admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        zones = list((await session.execute(select(GeoZone).order_by(GeoZone.tier, GeoZone.name))).scalars().all())
    return [
        {
            "id": str(z.id),
            "code": z.code,
            "name": z.name,
            "tier": z.tier,
            "is_active": z.is_active,
            "dispatch_fee": str(z.dispatch_fee),
            "base_lat": z.base_lat,
            "base_lon": z.base_lon,
            "free_radius_km": str(z.free_radius_km),
            "per_km_rate": str(z.per_km_rate),
        }
        for z in zones
    ]


@router.post("/geo-zones")
async def create_geo_zone(
    body: GeoZoneCreateRequest,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        z = GeoZone(**body.model_dump())
        session.add(z)
        await session.flush()
        await write_audit(
            session, admin=admin, action="geo_zone.create", entity_type="geo_zone", entity_id=str(z.id)
        )
        await session.commit()
        await session.refresh(z)
    return {"id": str(z.id), "code": z.code, "name": z.name, "is_active": z.is_active}


@router.patch("/geo-zones/{zone_id}")
async def patch_geo_zone(
    zone_id: uuid.UUID,
    body: GeoZonePatchRequest,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        z = await session.get(GeoZone, zone_id)
        if z is None:
            raise NotFoundError("Геозона не найдена")
        updates = body.model_dump(exclude_none=True)
        for k, v in updates.items():
            setattr(z, k, v)
        await write_audit(
            session,
            admin=admin,
            action="geo_zone.update",
            entity_type="geo_zone",
            entity_id=str(z.id),
            diff={k: str(v) for k, v in updates.items()},
        )
        await session.commit()
        await session.refresh(z)
    return {
        "id": str(z.id),
        "code": z.code,
        "name": z.name,
        "is_active": z.is_active,
        "dispatch_fee": str(z.dispatch_fee),
    }


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


@router.get("/executors")
async def list_executors(
    _admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        profiles = list((await session.execute(select(ExecutorProfile))).scalars().all())
        result: list[dict[str, Any]] = []
        for p in profiles:
            u = await session.get(User, p.user_id)
            accrued = (
                await session.execute(
                    select(func.coalesce(func.sum(PayoutLedgerEntry.amount), 0)).where(
                        PayoutLedgerEntry.executor_id == p.user_id,
                        PayoutLedgerEntry.status == LedgerEntryStatus.ACCRUED.value,
                    )
                )
            ).scalar() or ZERO
            result.append(
                {
                    "user_id": str(p.user_id),
                    "max_user_id": u.max_user_id if u else None,
                    "display_name": u.display_name if u else "",
                    "role": u.role if u else "",
                    "status": p.status,
                    "is_self_employed": p.is_self_employed,
                    "inn": p.inn,
                    "rating": str(p.rating),
                    "completed_orders_count": p.completed_orders_count,
                    "canceled_orders_count": p.canceled_orders_count,
                    "share_pct_override": (
                        str(p.share_pct_override) if p.share_pct_override is not None else None
                    ),
                    "max_active_orders": p.max_active_orders,
                    "has_snowblower": p.has_snowblower,
                    "has_miniloader": p.has_miniloader,
                    "has_tractor": p.has_tractor,
                    "roof_certified": p.roof_certified,
                    "insurance_policy_no": p.insurance_policy_no,
                    "insurance_valid_until": (
                        p.insurance_valid_until.isoformat() if p.insurance_valid_until else None
                    ),
                    "accrued_balance_rub": str(Decimal(accrued).quantize(Decimal("0.01"))),
                }
            )
    return result


@router.patch("/executors/{executor_id}/verify")
async def verify_executor(
    executor_id: uuid.UUID,
    body: ExecutorVerifyRequest,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    user_to_notify: User | None = None
    async with container.session_factory() as session:
        prof = await session.get(ExecutorProfile, executor_id)
        if prof is None:
            raise NotFoundError("Профиль исполнителя не найден")
        prof.status = body.status.value
        if body.notes is not None:
            prof.notes = body.notes
        if body.status == ExecutorStatus.ACTIVE:
            prof.verified_by = admin.id
            prof.verified_at = utcnow()
            user_to_notify = await session.get(User, executor_id)
        await write_audit(
            session,
            admin=admin,
            action="executor.verify",
            entity_type="executor",
            entity_id=str(executor_id),
            diff={"status": body.status.value},
        )
        await session.commit()

    if user_to_notify:
        await container.messenger.send_message(
            MessageTarget(user_id=user_to_notify.max_user_id),
            OutgoingMessage(
                text="🎉 **Ваш профиль исполнителя активирован!** Теперь вы можете брать заказы в общем чате бригады."
            ),
        )
    return {"user_id": str(executor_id), "status": body.status.value}


@router.patch("/executors/{executor_id}/permissions")
async def update_executor_permissions(
    executor_id: uuid.UUID,
    body: ExecutorPermissionsRequest,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        prof = await session.get(ExecutorProfile, executor_id)
        if prof is None:
            raise NotFoundError("Профиль исполнителя не найден")
        updates = body.model_dump(exclude_unset=True)
        for k, v in updates.items():
            setattr(prof, k, v)
        await write_audit(
            session,
            admin=admin,
            action="executor.permissions",
            entity_type="executor",
            entity_id=str(executor_id),
            diff={k: str(v) for k, v in updates.items()},
        )
        await session.commit()
    return {"user_id": str(executor_id), "updated": list(updates.keys())}


@router.post("/executors/{executor_id}/penalty")
async def apply_executor_penalty(
    executor_id: uuid.UUID,
    body: ExecutorPenaltyRequest,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        prof = await session.get(ExecutorProfile, executor_id)
        if prof is None:
            raise NotFoundError("Профиль исполнителя не найден")
        entry = PayoutLedgerEntry(
            executor_id=executor_id,
            order_id=body.order_id,
            entry_type=LedgerEntryType.PENALTY.value,
            amount=-abs(to_decimal(body.amount)),
            status=LedgerEntryStatus.ACCRUED.value,
            comment=body.reason,
            created_by_admin=admin.id,
        )
        session.add(entry)
        await write_audit(
            session,
            admin=admin,
            action="executor.penalty",
            entity_type="executor",
            entity_id=str(executor_id),
            diff={"amount": str(entry.amount), "reason": body.reason},
        )
        await session.commit()
        await session.refresh(entry)
    return {"entry_id": str(entry.id), "amount": str(entry.amount)}


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------


@router.get("/disputes")
async def list_disputes(
    _admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        disputes = list(
            (await session.execute(select(Dispute).order_by(Dispute.created_at.desc()))).scalars().all()
        )
        res = []
        for d in disputes:
            o = await session.get(Order, d.order_id)
            res.append(
                {
                    "id": str(d.id),
                    "order_id": str(d.order_id),
                    "order_public_number": o.public_number if o else None,
                    "reason": d.reason,
                    "status": d.status,
                    "resolution_note": d.resolution_note,
                    "discount_amount": str(d.discount_amount),
                    "refund_amount": str(d.refund_amount),
                    "created_at": d.created_at.isoformat(),
                    "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
                }
            )
    return res


@router.post("/disputes/{dispute_id}/resolve")
async def resolve_dispute_endpoint(
    dispute_id: uuid.UUID,
    body: DisputeResolveRequest,
    admin: AdminAccount = Depends(
        require_roles(AdminRole.FOREMAN, AdminRole.ADMIN, AdminRole.OWNER)
    ),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    if body.refund_amount > ZERO and admin.role == AdminRole.FOREMAN.value:
        raise ValidationAppError("Денежный возврат может оформить только Админ или Владелец")
    dispute = await container.orchestrator.resolve_dispute(
        dispute_id=dispute_id,
        resolution=body.resolution,
        note=body.note,
        admin_id=admin.id,
        discount_amount=body.discount_amount,
        refund_amount=body.refund_amount,
    )
    async with container.session_factory() as session:
        await write_audit(
            session,
            admin=admin,
            action="dispute.resolve",
            entity_type="dispute",
            entity_id=str(dispute_id),
            diff={"resolution": body.resolution.value, "refund": str(body.refund_amount)},
        )
        await session.commit()
    return {"id": str(dispute.id), "status": dispute.status}


# ---------------------------------------------------------------------------
# Payouts & Finance
# ---------------------------------------------------------------------------


@router.get("/payouts/balances")
async def payout_balances(
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        profiles = list((await session.execute(select(ExecutorProfile))).scalars().all())
        res = []
        for p in profiles:
            u = await session.get(User, p.user_id)
            accrued = (
                await session.execute(
                    select(func.coalesce(func.sum(PayoutLedgerEntry.amount), 0)).where(
                        PayoutLedgerEntry.executor_id == p.user_id,
                        PayoutLedgerEntry.status == LedgerEntryStatus.ACCRUED.value,
                    )
                )
            ).scalar() or ZERO
            paid = (
                await session.execute(
                    select(func.coalesce(func.sum(PayoutLedgerEntry.amount), 0)).where(
                        PayoutLedgerEntry.executor_id == p.user_id,
                        PayoutLedgerEntry.status == LedgerEntryStatus.PAID.value,
                    )
                )
            ).scalar() or ZERO
            res.append(
                {
                    "executor_id": str(p.user_id),
                    "display_name": u.display_name if u else "",
                    "inn": p.inn,
                    "accrued_rub": str(Decimal(accrued).quantize(Decimal("0.01"))),
                    "paid_rub": str(Decimal(paid).quantize(Decimal("0.01"))),
                }
            )
    return res


@router.get("/payouts/batches")
async def list_payout_batches(
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        batches = list(
            (
                await session.execute(
                    select(PayoutBatch).order_by(PayoutBatch.created_at.desc()).limit(100)
                )
            ).scalars().all()
        )
        res = []
        for b in batches:
            u = await session.get(User, b.executor_id)
            res.append(
                {
                    "id": str(b.id),
                    "executor_id": str(b.executor_id),
                    "executor_name": u.display_name if u else "",
                    "total_amount": str(b.total_amount),
                    "status": b.status,
                    "payment_method_note": b.payment_method_note,
                    "npd_receipt_id": b.npd_receipt_id,
                    "npd_receipt_url": b.npd_receipt_url,
                    "paid_at": b.paid_at.isoformat() if b.paid_at else None,
                    "created_at": b.created_at.isoformat(),
                }
            )
    return res


@router.post("/payouts/batches")
async def create_payout_batch(
    body: CreatePayoutBatchRequest,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    batch = await container.orchestrator.create_payout_batch(
        executor_id=body.executor_id, admin_id=admin.id
    )
    async with container.session_factory() as session:
        await write_audit(
            session,
            admin=admin,
            action="payout_batch.create",
            entity_type="payout_batch",
            entity_id=str(batch.id),
            diff={"total_amount": str(batch.total_amount)},
        )
        await session.commit()
    return {"id": str(batch.id), "total_amount": str(batch.total_amount), "status": batch.status}


@router.post("/payouts/batches/{batch_id}/mark-paid")
async def mark_payout_batch_paid(
    batch_id: uuid.UUID,
    body: MarkBatchPaidRequest,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    batch = await container.orchestrator.mark_payout_batch_paid(
        batch_id=batch_id,
        admin_id=admin.id,
        payment_method_note=body.payment_method_note,
        npd_receipt_id=body.npd_receipt_id,
        npd_receipt_url=body.npd_receipt_url,
    )
    async with container.session_factory() as session:
        await write_audit(
            session,
            admin=admin,
            action="payout_batch.mark_paid",
            entity_type="payout_batch",
            entity_id=str(batch.id),
            diff={"npd_receipt_id": body.npd_receipt_id, "npd_receipt_url": body.npd_receipt_url},
        )
        await session.commit()
    return {"id": str(batch.id), "status": batch.status, "paid_at": batch.paid_at.isoformat() if batch.paid_at else None}


@router.get("/payouts/batches/export.csv")
async def export_payout_batches_csv(
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> Response:
    async with container.session_factory() as session:
        batches = list(
            (await session.execute(select(PayoutBatch).order_by(PayoutBatch.created_at.desc()))).scalars().all()
        )
        users = {u.id: u for u in (await session.execute(select(User))).scalars().all()}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["batch_id", "executor", "total_rub", "status", "payment_note", "npd_receipt_id", "npd_receipt_url", "paid_at"]
    )
    for b in batches:
        u = users.get(b.executor_id)
        writer.writerow(
            [
                str(b.id),
                u.display_name if u else str(b.executor_id),
                str(b.total_amount),
                b.status,
                b.payment_method_note or "",
                b.npd_receipt_id or "",
                b.npd_receipt_url or "",
                b.paid_at.isoformat() if b.paid_at else "",
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sneg_payouts.csv"'},
    )


# ---------------------------------------------------------------------------
# Bot Chats, Broadcasts & Audit Logs
# ---------------------------------------------------------------------------


@router.get("/bot-chats")
async def list_bot_chats(
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        chats = list((await session.execute(select(BotChat))).scalars().all())
    return [
        {
            "chat_id": c.chat_id,
            "title": c.title,
            "chat_purpose": c.chat_purpose,
            "is_active": c.is_active,
            "bot_is_admin": c.bot_is_admin,
        }
        for c in chats
    ]


@router.patch("/bot-chats/{chat_id}")
async def patch_bot_chat(
    chat_id: int,
    body: BotChatPatchRequest,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        chat = await session.get(BotChat, chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(chat, k, v)
        await write_audit(
            session, admin=admin, action="bot_chat.update", entity_type="bot_chat", entity_id=str(chat_id)
        )
        await session.commit()
    return {"chat_id": chat_id, "chat_purpose": chat.chat_purpose, "is_active": chat.is_active}


@router.post("/broadcasts")
async def send_broadcast(
    body: BroadcastRequest,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, int]:
    async with container.session_factory() as session:
        stmt = select(User).where(User.is_blocked.is_(False))
        if body.target_role != "all":
            stmt = stmt.where(User.role == body.target_role)
        # Исключаем пользователей, отписавшихся от рассылок (38-ФЗ / 152-ФЗ)
        unsubscribed_ids = set(
            (
                await session.execute(
                    select(UserConsent.user_id).where(
                        UserConsent.consent_type == "marketing_revoked"
                    )
                )
            )
            .scalars()
            .all()
        )
        users = [
            u
            for u in (await session.execute(stmt)).scalars().all()
            if u.id not in unsubscribed_ids and u.max_user_id > 0
        ]
        await write_audit(
            session,
            admin=admin,
            action="broadcast.send",
            entity_type="broadcast",
            entity_id=body.target_role,
            diff={"recipients": len(users), "excluded_unsubscribed": len(unsubscribed_ids)},
        )
        await session.commit()

    outgoing_text = body.text
    if "/unsubscribe" not in outgoing_text:
        outgoing_text = (
            f"{outgoing_text}\n\n—\nОтписаться от рассылок: отправьте /unsubscribe"
        )

    sent = 0
    for u in users:
        await container.messenger.send_message(
            MessageTarget(user_id=u.max_user_id), OutgoingMessage(text=outgoing_text)
        )
        sent += 1
    return {"sent": sent, "excluded_unsubscribed": len(unsubscribed_ids)}


class ResolveDsrRequest(BaseModel):
    status: str = Field(default="completed", pattern="^(completed|rejected)$")
    anonymize_user: bool = False
    resolution_note: str = Field(default="", max_length=1000)


@router.get("/data-requests")
async def list_data_requests(
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(DataSubjectRequest)
                    .order_by(DataSubjectRequest.created_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": str(r.id),
            "ticket_code": r.ticket_code,
            "request_type": r.request_type,
            "status": r.status,
            "contact_value": r.contact_value,
            "max_user_id": r.max_user_id,
            "user_id": str(r.user_id) if r.user_id else None,
            "details": r.details,
            "source": r.source,
            "resolution_note": r.resolution_note,
            "created_at": r.created_at.isoformat(),
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in rows
    ]


@router.post("/data-requests/{request_id}/resolve")
async def resolve_data_request(
    request_id: uuid.UUID,
    body: ResolveDsrRequest,
    request: Request,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    now = utcnow()
    anonymized = False
    async with container.session_factory() as session:
        dsr = await session.get(DataSubjectRequest, request_id)
        if dsr is None:
            raise NotFoundError("Запрос субъекта ПДн не найден")

        dsr.status = body.status
        dsr.handled_by = admin.id
        dsr.resolution_note = body.resolution_note.strip() or (
            "Исполнено администратором по 152-ФЗ"
            if body.status == "completed"
            else "Отклонено администратором"
        )
        dsr.resolved_at = now

        if body.status == "completed" and body.anonymize_user and dsr.user_id is not None:
            user = await session.get(User, dsr.user_id)
            if user is not None:
                user.first_name = "Удалённый пользователь"
                user.last_name = None
                user.username = None
                user.bot_phone = None
                user.shared_contact_phone = None
                user.email = None
                user.updated_at = now
                anonymized = True

        await write_audit(
            session,
            admin=admin,
            action="privacy.dsr_resolve",
            entity_type="data_subject_request",
            entity_id=str(dsr.id),
            diff={
                "ticket_code": dsr.ticket_code,
                "status": dsr.status,
                "anonymized": anonymized,
            },
            ip=request.client.host if request.client else None,
        )
        await session.commit()

    return {
        "id": str(request_id),
        "status": body.status,
        "anonymized": anonymized,
    }


@router.get("/audit-logs")
async def list_audit_logs(
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        logs = list(
            (await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100))).scalars().all()
        )
        admins = {a.id: a.email for a in (await session.execute(select(AdminAccount))).scalars().all()}
    return [
        {
            "id": str(log.id),
            "admin_email": admins.get(log.admin_id, "system") if log.admin_id else "system",
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "diff_json": log.diff_json,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


# ---------------------------------------------------------------------------
# Media Proxy (для отображения сохранённых в S3/Local фото в админке и симуляторе)
# ---------------------------------------------------------------------------


@media_router.get("/file/{media_id}")
async def serve_order_media(
    media_id: uuid.UUID,
    container: AppContainer = Depends(get_container),
) -> Response:
    async with container.session_factory() as session:
        media = await session.get(OrderMedia, media_id)
        if media is None:
            raise NotFoundError("Медиафайл не найден")
        storage_key = media.storage_key
        mime_type = media.mime_type
    raw = await container.storage.get_object(storage_key)
    return Response(content=raw, media_type=mime_type)
