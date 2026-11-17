"""Publish MO 2026 market tariff as v2 on existing databases still running initial v1 seed.

Revision ID: 0003_market_2026_tariffs
Revises: 0002_add_data_subject_requests
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

revision: str = "0003_market_2026_tariffs"
down_revision: str | None = "0002_add_data_subject_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_COMMENT = "Рыночный прайс МО 2026 (Люберцы / Малаховка, автомиграция v2)"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tariff_configs" not in inspector.get_table_names():
        return

    rows = bind.execute(
        sa.text("SELECT id, version, is_active, config_json FROM tariff_configs ORDER BY version ASC")
    ).fetchall()

    # Свежая БД (тарифы ещё не посеяны) — seed_initial_data сразу создаст v1 с тарифами МО 2026
    if not rows:
        return

    # Если владелец уже публиковал v2+ вручную — не трогаем пользовательские тарифы
    max_version = max(int(r[1]) for r in rows)
    if max_version >= 2:
        return

    v1_id, _v1_ver, v1_active, v1_raw_cfg = rows[0]
    v1_cfg = json.loads(v1_raw_cfg) if isinstance(v1_raw_cfg, str) else (v1_raw_cfg or {})
    old_manual_rate = (
        v1_cfg.get("snow", {}).get("methods", {}).get("manual", {}).get("rate_per_m2")
    )

    # Обновляем только если v1 действительно содержит старый стартовый прайс (25 ₽/м²)
    if old_manual_rate != 25:
        return

    if v1_active:
        bind.execute(
            sa.text("UPDATE tariff_configs SET is_active = :inactive WHERE id = :id"),
            {"inactive": False, "id": v1_id},
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
            version=2,
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

    v2_row = bind.execute(
        sa.text("SELECT id FROM tariff_configs WHERE version = 2 AND comment = :comment"),
        {"comment": MIGRATION_COMMENT},
    ).fetchone()
    if v2_row is None:
        return

    orders_on_v2 = bind.execute(
        sa.text("SELECT COUNT(1) FROM orders WHERE tariff_config_id = :tid"),
        {"tid": v2_row[0]},
    ).scalar()
    if orders_on_v2:
        return

    bind.execute(sa.text("DELETE FROM tariff_configs WHERE id = :tid"), {"tid": v2_row[0]})
    bind.execute(sa.text("UPDATE tariff_configs SET is_active = :active WHERE version = 1"), {"active": True})
