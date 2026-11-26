"""Внутренние черновики: без связи с заказами, договорами и реальными платежами."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_container, require_roles, write_audit
from src.core.container import AppContainer
from src.core.enums import AdminRole, ServiceType
from src.core.exceptions import InvalidStateTransitionError, NotFoundError
from src.domain.pricing.payment_plan import (
    FinalPaymentRoute,
    PaymentSchedule,
    estimate_payment_plan,
)
from src.infra.db.models import AdminAccount, MarketplaceDraft, MarketplaceDraftVersion, utcnow

router = APIRouter(prefix="/api/v1/admin/marketplace/drafts", tags=["marketplace-drafts"])


class DraftTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_type: ServiceType
    total: Decimal
    schedule: PaymentSchedule
    final_payment_route: FinalPaymentRoute | None = None


class RevisionTerms(DraftTerms):
    expected_revision: int = Field(ge=1)


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


def _version(
    body: DraftTerms, draft_id: uuid.UUID, revision: int, admin: AdminAccount
) -> MarketplaceDraftVersion:
    plan = estimate_payment_plan(
        body.total, schedule=body.schedule, final_payment_route=body.final_payment_route
    )
    return MarketplaceDraftVersion(
        draft_id=draft_id,
        revision=revision,
        model_version="marketplace_preview_v1",
        service_type=body.service_type.value,
        total=plan.estimate.total,
        commission_pct=plan.estimate.commission_percent,
        commission=plan.estimate.commission,
        executor_after_commission=plan.estimate.executor_after_commission,
        schedule=plan.schedule.value,
        final_payment_route=plan.final_payment_route.value if plan.final_payment_route else None,
        upfront=plan.upfront,
        after_completion=plan.after_completion,
        created_by=admin.id,
    )


def _serialize_version(v: MarketplaceDraftVersion) -> dict[str, Any]:
    return {
        "revision": v.revision,
        "model_version": v.model_version,
        "service_type": v.service_type,
        "total": str(v.total),
        "commission_pct": str(v.commission_pct),
        "commission": str(v.commission),
        "executor_after_commission": str(v.executor_after_commission),
        "schedule": v.schedule,
        "final_payment_route": v.final_payment_route,
        "upfront": str(v.upfront),
        "after_completion": str(v.after_completion),
        "created_at": v.created_at.isoformat(),
    }


def _serialize_draft(draft: MarketplaceDraft) -> dict[str, Any]:
    return {
        "id": str(draft.id),
        "status": draft.status,
        "revision": draft.revision,
        "created_at": draft.created_at.isoformat(),
        "updated_at": draft.updated_at.isoformat(),
    }


async def _claim_revision(
    session: AsyncSession, draft_id: uuid.UUID, expected: int, *, archive: bool
) -> None:
    """CAS при конкурирующих изменениях: не перезаписывать условия незаметно."""
    values: dict[str, Any] = {"updated_at": utcnow()}
    if archive:
        values["status"] = "archived"
    else:
        values["revision"] = expected + 1
    result = await session.execute(
        update(MarketplaceDraft)
        .where(
            MarketplaceDraft.id == draft_id,
            MarketplaceDraft.status == "draft",
            MarketplaceDraft.revision == expected,
        )
        .values(**values)
        .returning(MarketplaceDraft.id)
    )
    if result.scalar_one_or_none() is None:
        if await session.get(MarketplaceDraft, draft_id) is None:
            raise NotFoundError("Черновик не найден")
        raise InvalidStateTransitionError(
            "Черновик уже изменён или архивирован. Обновите страницу."
        )


@router.get("")
async def list_drafts(
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    async with container.session_factory() as session:
        drafts = (
            (
                await session.execute(
                    select(MarketplaceDraft).order_by(MarketplaceDraft.created_at.desc()).limit(100)
                )
            )
            .scalars()
            .all()
        )
        return [_serialize_draft(d) for d in drafts]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_draft(
    body: DraftTerms,
    request: Request,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    draft = MarketplaceDraft(id=uuid.uuid4(), status="draft", revision=1, created_by=admin.id)
    version = _version(body, draft.id, 1, admin)
    async with container.session_factory() as session:
        session.add(draft)
        await session.flush()
        session.add(version)
        await write_audit(
            session,
            admin=admin,
            action="marketplace_draft.create",
            entity_type="marketplace_draft",
            entity_id=str(draft.id),
            diff={"revision": 1},
            ip=request.client.host if request.client else None,
        )
        await session.commit()
        await session.refresh(draft)
        await session.refresh(version)
        return {**_serialize_draft(draft), "versions": [_serialize_version(version)]}


@router.get("/{draft_id}")
async def get_draft(
    draft_id: uuid.UUID,
    _admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        draft = await session.get(MarketplaceDraft, draft_id)
        if draft is None:
            raise NotFoundError("Черновик не найден")
        versions = (
            (
                await session.execute(
                    select(MarketplaceDraftVersion)
                    .where(MarketplaceDraftVersion.draft_id == draft_id)
                    .order_by(MarketplaceDraftVersion.revision)
                )
            )
            .scalars()
            .all()
        )
        return {**_serialize_draft(draft), "versions": [_serialize_version(v) for v in versions]}


@router.post("/{draft_id}/versions", status_code=status.HTTP_201_CREATED)
async def revise_draft(
    draft_id: uuid.UUID,
    body: RevisionTerms,
    request: Request,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    version = _version(body, draft_id, body.expected_revision + 1, admin)
    async with container.session_factory() as session:
        await _claim_revision(session, draft_id, body.expected_revision, archive=False)
        session.add(version)
        await write_audit(
            session,
            admin=admin,
            action="marketplace_draft.revise",
            entity_type="marketplace_draft",
            entity_id=str(draft_id),
            diff={"revision": version.revision},
            ip=request.client.host if request.client else None,
        )
        await session.commit()
        await session.refresh(version)
        return _serialize_version(version)


@router.post("/{draft_id}/archive")
async def archive_draft(
    draft_id: uuid.UUID,
    body: ArchiveRequest,
    request: Request,
    admin: AdminAccount = Depends(require_roles(AdminRole.ADMIN, AdminRole.OWNER)),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.session_factory() as session:
        await _claim_revision(session, draft_id, body.expected_revision, archive=True)
        await write_audit(
            session,
            admin=admin,
            action="marketplace_draft.archive",
            entity_type="marketplace_draft",
            entity_id=str(draft_id),
            diff={"revision": body.expected_revision},
            ip=request.client.host if request.client else None,
        )
        await session.commit()
    return {"id": str(draft_id), "status": "archived", "revision": body.expected_revision}
