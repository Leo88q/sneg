"""SQLAlchemy 2.0 ORM-модели всех 23 таблиц (строго по docs/02_database_schema.sql)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infra.db.session import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")
AutoBigInt = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    max_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    max_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="client")
    # Строго по правилам MAX: bot_phone используется ТОЛЬКО внутри бота
    bot_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Отдельный телефон, введённый пользователем вручную с согласием phone_transfer
    shared_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    executor_profile: Mapped[ExecutorProfile | None] = relationship(
        back_populates="user", uselist=False
    )

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) if parts else f"User #{self.max_user_id}"


class AdminAccount(Base):
    __tablename__ = "admin_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    linked_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class UserConsent(Base):
    __tablename__ = "user_consents"
    __table_args__ = (Index("idx_user_consents_user_type", "user_id", "consent_type", "accepted_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    consent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    document_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="max_bot")
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ExecutorProfile(Base):
    __tablename__ = "executor_profiles"
    __table_args__ = (
        CheckConstraint("max_active_orders >= 1 AND max_active_orders <= 10"),
        CheckConstraint("rating >= 1.00 AND rating <= 5.00"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_verification")
    is_self_employed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    share_pct_override: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    max_active_orders: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("5.00"))
    ratings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    canceled_orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_snowblower: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_miniloader: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_tractor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    roof_certified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    insurance_policy_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    insurance_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="executor_profile")


class BotChat(Base):
    __tablename__ = "bot_chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bot_is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class GeoZone(Base):
    __tablename__ = "geo_zones"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="core")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dispatch_fee: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    base_lat: Mapped[float] = mapped_column(Float, nullable=False)
    base_lon: Mapped[float] = mapped_column(Float, nullable=False)
    free_radius_km: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("5.00")
    )
    per_km_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("100.00")
    )
    polygon_geojson: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class TariffConfig(Base):
    __tablename__ = "tariff_configs"
    __table_args__ = (
        Index(
            "uq_tariff_configs_single_active",
            "is_active",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active = TRUE"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    geo_zone_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("geo_zones.id", ondelete="RESTRICT"), nullable=False
    )
    plan_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_payment")
    address_text: Mapped[str] = mapped_column(Text, nullable=False)
    public_area_hint: Mapped[str] = mapped_column(String(255), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_m2: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    cleaning_method: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    access_constraint: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    snow_trigger_cm: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("5.00")
    )
    preferred_time_window: Mapped[str] = mapped_column(
        String(64), nullable=False, default="07:00-10:00"
    )
    auto_confirm_hours: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=4)
    price_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class OrderGroup(Base):
    __tablename__ = "order_groups"
    __table_args__ = (UniqueConstraint("service_date", "geo_zone_id", "cluster_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    geo_zone_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("geo_zones.id", ondelete="RESTRICT"), nullable=False
    )
    cluster_key: Mapped[str] = mapped_column(String(255), nullable=False)
    orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_status_created", "status", "created_at"),
        Index("idx_orders_client_created", "client_id", "created_at"),
        CheckConstraint("required_executors >= 1 AND required_executors <= 10"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_number: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    service_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unpaid")
    geo_zone_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("geo_zones.id", ondelete="RESTRICT"), nullable=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    order_group_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("order_groups.id", ondelete="SET NULL"), nullable=True
    )

    address_text: Mapped[str] = mapped_column(Text, nullable=False)
    public_area_hint: Mapped[str] = mapped_column(String(255), nullable=False)
    cluster_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_from_base_km: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)

    params_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    cleaning_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    urgency: Mapped[str] = mapped_column(String(32), nullable=False, default="today")
    required_executors: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tariff_config_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tariff_configs.id", ondelete="RESTRICT"), nullable=False
    )
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    estimated_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    final_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    advance_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    group_discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    executor_bonus_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )

    dispatch_wave: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    dispatch_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dispatch_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    contact_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="bot_proxy")
    client_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    client_review: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrderAssignment(Base):
    __tablename__ = "order_assignments"
    __table_args__ = (
        Index(
            "uq_order_assignments_active",
            "order_id",
            "executor_id",
            unique=True,
            sqlite_where=text(
                "status IN ('assigned', 'en_route', 'arrived', 'in_progress', 'completed')"
            ),
            postgresql_where=text(
                "status IN ('assigned', 'en_route', 'arrived', 'in_progress', 'completed')"
            ),
        ),
        Index("idx_order_assignments_executor_status", "executor_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    executor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("executor_profiles.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role_in_order: Mapped[str] = mapped_column(String(32), nullable=False, default="primary")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="assigned")
    executor_share_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    en_route_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderMedia(Base):
    __tablename__ = "order_media"
    __table_args__ = (Index("idx_order_media_order_stage", "order_id", "stage"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    max_photo_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False, default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class PriceAdjustment(Base):
    __tablename__ = "price_adjustments"
    __table_args__ = (
        Index(
            "uq_price_adjustments_pending",
            "order_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    proposed_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    old_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    new_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    delta_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    client_responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Dispute(Base):
    __tablename__ = "disputes"
    __table_args__ = (Index("idx_disputes_status", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    opened_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_admin: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    refund_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    __table_args__ = (Index("idx_order_status_history_order", "order_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class OrderMessage(Base):
    __tablename__ = "order_messages"
    __table_args__ = (Index("idx_order_messages_order", "order_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    sender_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        Index("idx_payments_order", "order_id", "purpose"),
        CheckConstraint("(order_id IS NOT NULL) OR (subscription_id IS NOT NULL)"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("subscriptions.id", ondelete="RESTRICT"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    confirmation_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    provider_refund_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PayoutBatch(Base):
    __tablename__ = "payout_batches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    executor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("executor_profiles.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    payment_method_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    npd_receipt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    npd_receipt_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class PayoutLedgerEntry(Base):
    __tablename__ = "payout_ledger_entries"
    __table_args__ = (Index("idx_payout_ledger_executor_status", "executor_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    executor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("executor_profiles.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="accrued")
    payout_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("payout_batches.id", ondelete="SET NULL"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_admin: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ProcessedWebhookEvent(Base):
    __tablename__ = "processed_webhook_events"
    __table_args__ = (UniqueConstraint("source", "event_key"),)

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("idx_audit_logs_entity", "entity_type", "entity_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    diff_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class SimulatorMessage(Base):
    __tablename__ = "simulator_messages"
    __table_args__ = (Index("idx_simulator_messages_chat", "chat_id", "id"),)

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_role: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_max_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_message_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attachments_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    is_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class DataSubjectRequest(Base):
    """Запросы субъектов ПДн (152-ФЗ ст. 14, 21: удаление, выгрузка, отзыв согласия, отписка)."""

    __tablename__ = "data_subject_requests"
    __table_args__ = (Index("idx_dsr_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    contact_value: Mapped[str] = mapped_column(String(255), nullable=False)
    max_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    age_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="web")
    handled_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)



class MarketplaceDraft(Base):
    """Внутренний черновик условий, не заказ и не договор."""

    __tablename__ = "marketplace_drafts"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_marketplace_draft_revision"),
        CheckConstraint("status IN ('draft', 'archived')", name="ck_marketplace_draft_status"),
        Index("idx_marketplace_drafts_created", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MarketplaceDraftVersion(Base):
    """Добавляемая версия предварительных условий без персональных данных."""

    __tablename__ = "marketplace_draft_versions"
    __table_args__ = (
        UniqueConstraint("draft_id", "revision", name="uq_marketplace_draft_revision"),
        CheckConstraint("revision >= 1", name="ck_marketplace_version_revision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("marketplace_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    service_type: Mapped[str] = mapped_column(String(32), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    commission_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    executor_after_commission: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    schedule: Mapped[str] = mapped_column(String(32), nullable=False)
    final_payment_route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upfront: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    after_completion: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
