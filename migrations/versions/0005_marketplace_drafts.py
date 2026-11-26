"""Isolated marketplace draft versions; no changes to existing orders.

Revision ID: 0005_marketplace_drafts
Revises: 0004_subcontract_rental_tariffs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_marketplace_drafts"
down_revision: str | None = "0004_subcontract_rental_tariffs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 uses current metadata on fresh installs, so tables may already exist.
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "marketplace_drafts" not in tables:
        op.create_table(
            "marketplace_drafts",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column(
                "created_by", sa.Uuid(), sa.ForeignKey("admin_accounts.id", ondelete="SET NULL")
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("revision >= 1", name="ck_marketplace_draft_revision"),
            sa.CheckConstraint(
                "status IN ('draft', 'archived')", name="ck_marketplace_draft_status"
            ),
        )
        op.create_index("idx_marketplace_drafts_created", "marketplace_drafts", ["created_at"])
    if "marketplace_draft_versions" not in tables:
        op.create_table(
            "marketplace_draft_versions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "draft_id",
                sa.Uuid(),
                sa.ForeignKey("marketplace_drafts.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("model_version", sa.String(32), nullable=False),
            sa.Column("service_type", sa.String(32), nullable=False),
            sa.Column("total", sa.Numeric(12, 2), nullable=False),
            sa.Column("commission_pct", sa.Numeric(5, 2), nullable=False),
            sa.Column("commission", sa.Numeric(12, 2), nullable=False),
            sa.Column("executor_after_commission", sa.Numeric(12, 2), nullable=False),
            sa.Column("schedule", sa.String(32), nullable=False),
            sa.Column("final_payment_route", sa.String(32), nullable=True),
            sa.Column("upfront", sa.Numeric(12, 2), nullable=False),
            sa.Column("after_completion", sa.Numeric(12, 2), nullable=False),
            sa.Column(
                "created_by", sa.Uuid(), sa.ForeignKey("admin_accounts.id", ondelete="SET NULL")
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("draft_id", "revision", name="uq_marketplace_draft_revision"),
            sa.CheckConstraint("revision >= 1", name="ck_marketplace_version_revision"),
        )


def downgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "marketplace_draft_versions" in tables:
        op.drop_table("marketplace_draft_versions")
    if "marketplace_drafts" in tables:
        op.drop_index("idx_marketplace_drafts_created", table_name="marketplace_drafts")
        op.drop_table("marketplace_drafts")
