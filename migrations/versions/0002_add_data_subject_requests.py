"""Add data_subject_requests table for 152-FZ (art. 14, 21) privacy/deletion/unsubscribe requests.

Revision ID: 0002_add_data_subject_requests
Revises: 0001_initial_schema
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_data_subject_requests"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "data_subject_requests" not in inspector.get_table_names():
        op.create_table(
            "data_subject_requests",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("ticket_code", sa.String(length=32), nullable=False),
            sa.Column("request_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("contact_value", sa.String(length=255), nullable=False),
            sa.Column("max_user_id", sa.BigInteger(), nullable=True),
            sa.Column(
                "user_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("consent_accepted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "age_confirmed", sa.Boolean(), nullable=False, server_default=sa.text("true")
            ),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="web"),
            sa.Column(
                "handled_by",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("admin_accounts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("resolution_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_data_subject_requests_ticket_code",
            "data_subject_requests",
            ["ticket_code"],
            unique=True,
        )
        op.create_index(
            "idx_dsr_status_created",
            "data_subject_requests",
            ["status", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "data_subject_requests" in inspector.get_table_names():
        op.drop_index("idx_dsr_status_created", table_name="data_subject_requests")
        op.drop_index("ix_data_subject_requests_ticket_code", table_name="data_subject_requests")
        op.drop_table("data_subject_requests")
