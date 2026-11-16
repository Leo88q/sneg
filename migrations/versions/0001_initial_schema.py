"""Initial schema for Sneg-Dispatcher (all 23 tables from TZ v2.0 B4).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from src.infra.db import models  # noqa: F401
from src.infra.db.session import Base

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
