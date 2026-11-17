"""Publish MO 2026 subcontract/rental tariffs on existing DBs still running earlier auto-seeded tariffs.

Revision ID: 0004_subcontract_rental_tariffs
Revises: 0003_market_2026_tariffs
Create Date: 2026-11-17
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from src.domain.pricing.defaults import get_default_tariff_config

revision: str = "0004_subcontract_rental_tariffs"
down_revision: str | None = "0003_market_2026_tariffs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_COMMENT = "Прайс МО 2026 с учётом аренды техники на субподряде (автомиграция)"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tariff_configs" not in inspector.get_table_names():
        return

    rows = bind.execute(
        sa.text(
            "SELECT id, version, is_active, config_json, comment "
            "FROM tariff_configs ORDER BY version ASC"
        )
    ).fetchall()
    if not rows:
        return

    active_rows = [r for r in rows if bool(r[2])]
    if not active_rows:
        return

    active_id, _active_ver, _is_act, raw_cfg, comment = active_rows[-1]
    cfg = json.loads(raw_cfg) if isinstance(raw_cfg, str) else (raw_cfg or {})
    snow_methods = cfg.get("snow", {}).get("methods", {})
    tractor_min = snow_methods.get("tractor", {}).get("min_work_price")
    manual_rate = snow_methods.get("manual", {}).get("rate_per_m2")

    # Если уже активен тариф с заложенной арендой техники — ничего не делаем
    if tractor_min is not None and float(tractor_min) >= 14000:
        return

    # Обновляем только автопосеянные/автомигрированные версии, не трогая ручные правки владельца
    comment_str = str(comment or "")
    is_system_tariff = (
        manual_rate == 25
        or tractor_min == 5000
        or "автомиграция" in comment_str
        or "Стартовая конфигурация" in comment_str
        or "Рыночный прайс МО 2026" in comment_str
    )
    if not is_system_tariff:
        return

    max_version = max(int(r[1]) for r in rows)

    bind.execute(
        sa.text("UPDATE tariff_configs SET is_active = :inactive WHERE id = :id"),
        {"inactive": False, "id": active_id},
    )

    tariff_table = sa.table(
        "tariff_configs",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("version", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("config_json", sa.JSON()),
        sa.column("comment", sa.Text()),
        sa.column("updated_by", sa.Uuid(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    bind.execute(
        tariff_table.insert().values(
            id=uuid.uuid4(),
            version=max_version + 1,
            is_active=True,
            config_json=get_default_tariff_config(),
            comment=MIGRATION_COMMENT,
            updated_by=None,
            created_at=datetime.now(UTC),
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tariff_configs" not in inspector.get_table_names():
        return

    mig_row = bind.execute(
        sa.text("SELECT id, version FROM tariff_configs WHERE comment = :comment ORDER BY version DESC"),
        {"comment": MIGRATION_COMMENT},
    ).fetchone()
    if mig_row is None:
        return

    orders_on_mig = bind.execute(
        sa.text("SELECT COUNT(1) FROM orders WHERE tariff_config_id = :tid"),
        {"tid": mig_row[0]},
    ).scalar()
    if orders_on_mig:
        return

    prev_row = bind.execute(
        sa.text("SELECT id FROM tariff_configs WHERE version < :ver ORDER BY version DESC LIMIT 1"),
        {"ver": mig_row[1]},
    ).fetchone()

    bind.execute(sa.text("DELETE FROM tariff_configs WHERE id = :tid"), {"tid": mig_row[0]})
    if prev_row is not None:
        bind.execute(
            sa.text("UPDATE tariff_configs SET is_active = :active WHERE id = :id"),
            {"active": True, "id": prev_row[0]},
        )
