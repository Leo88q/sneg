"""Служебные эндпоинты: /health, /ready, /metrics (Prometheus text)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select, text

from src.api.deps import get_container
from src.core.container import AppContainer
from src.infra.db.models import Dispute, Order

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(container: AppContainer = Depends(get_container)) -> dict[str, str]:
    async with container.session_factory() as session:
        await session.execute(text("SELECT 1"))
    return {
        "status": "ready",
        "messenger": container.settings.messenger_transport,
        "payment_provider": container.settings.payment_provider,
        "storage": container.settings.storage_provider,
    }


@router.get("/metrics")
async def metrics(container: AppContainer = Depends(get_container)) -> Response:
    async with container.session_factory() as session:
        rows = (
            await session.execute(select(Order.status, func.count(Order.id)).group_by(Order.status))
        ).all()
        open_disputes = (
            await session.execute(
                select(func.count(Dispute.id)).where(Dispute.status.in_(["open", "in_review"]))
            )
        ).scalar() or 0

    lines = [
        "# HELP sneg_orders_total Number of orders by status",
        "# TYPE sneg_orders_total gauge",
    ]
    for status, cnt in rows:
        lines.append(f'sneg_orders_total{{status="{status}"}} {cnt}')
    lines.extend(
        [
            "# HELP sneg_open_disputes Number of open disputes",
            "# TYPE sneg_open_disputes gauge",
            f"sneg_open_disputes {open_disputes}",
        ]
    )
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
