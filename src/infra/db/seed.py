"""Идемпотентная инициализация справочников, владельца и демо-персонажей симулятора."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.enums import (
    AdminRole,
    ChatPurpose,
    ConsentType,
    ExecutorStatus,
    UserRole,
)
from src.core.security import hash_password
from src.domain.pricing.defaults import DEFAULT_GEO_ZONES, get_default_tariff_config
from src.infra.db.models import (
    AdminAccount,
    BotChat,
    ExecutorProfile,
    GeoZone,
    TariffConfig,
    User,
    UserConsent,
)

# Предопределённые виртуальные MAX ID для интерактивного симулятора
SIM_DISPATCH_CHAT_ID = -10090001
SIM_PERSONAS: dict[str, dict[str, Any]] = {
    "client_1": {
        "max_user_id": 10001,
        "max_chat_id": 10001,
        "first_name": "Анна",
        "last_name": "Смирнова",
        "username": "anna_malakhovka",
        "role": UserRole.CLIENT.value,
        "email": "anna@example.ru",
    },
    "client_2": {
        "max_user_id": 10002,
        "max_chat_id": 10002,
        "first_name": "Дмитрий",
        "last_name": "Волков",
        "username": "dmitry_neighbor",
        "role": UserRole.CLIENT.value,
        "email": "dmitry@example.ru",
    },
    "executor_1": {
        "max_user_id": 20001,
        "max_chat_id": 20001,
        "first_name": "Иван",
        "last_name": "Петров",
        "username": "ivan_brigada",
        "role": UserRole.EXECUTOR.value,
        "has_snowblower": True,
        "has_miniloader": True,
        "has_tractor": False,
        "roof_certified": False,
        "inn": "502712345678",
        "rating": Decimal("4.90"),
    },
    "executor_2": {
        "max_user_id": 20002,
        "max_chat_id": 20002,
        "first_name": "Сергей",
        "last_name": "Кузнецов",
        "username": "sergey_tractor",
        "role": UserRole.EXECUTOR.value,
        "has_snowblower": True,
        "has_miniloader": False,
        "has_tractor": True,
        "roof_certified": True,
        "inn": "502798765432",
        "rating": Decimal("4.75"),
    },
    "foreman_1": {
        "max_user_id": 30001,
        "max_chat_id": 30001,
        "first_name": "Алексей",
        "last_name": "Бригадир",
        "username": "alex_foreman",
        "role": UserRole.FOREMAN.value,
        "has_snowblower": True,
        "has_miniloader": True,
        "has_tractor": True,
        "roof_certified": True,
        "inn": "502755511122",
        "rating": Decimal("5.00"),
    },
}


async def seed_initial_data(session: AsyncSession, settings: Settings) -> None:
    # 1. Геозоны
    existing_zones = {
        z.code: z for z in (await session.execute(select(GeoZone))).scalars().all()
    }
    for zone_data in DEFAULT_GEO_ZONES:
        if zone_data["code"] not in existing_zones:
            session.add(
                GeoZone(
                    code=zone_data["code"],
                    name=zone_data["name"],
                    tier=zone_data["tier"],
                    is_active=zone_data["is_active"],
                    dispatch_fee=Decimal(zone_data["dispatch_fee"]),
                    base_lat=zone_data["base_lat"],
                    base_lon=zone_data["base_lon"],
                    free_radius_km=Decimal(zone_data["free_radius_km"]),
                    per_km_rate=Decimal(zone_data["per_km_rate"]),
                )
            )

    # 2. Стартовый активный тариф v1
    active_tariff = (
        await session.execute(select(TariffConfig).where(TariffConfig.is_active.is_(True)))
    ).scalar_one_or_none()
    if active_tariff is None:
        session.add(
            TariffConfig(
                version=1,
                is_active=True,
                config_json=get_default_tariff_config(),
                comment="Стартовая конфигурация тарифов (ТЗ v2.0, раздел B7.1)",
            )
        )

    # 3. Учётная запись владельца для веб-админки
    owner = (
        await session.execute(
            select(AdminAccount).where(AdminAccount.email == settings.initial_owner_email)
        )
    ).scalar_one_or_none()
    if owner is None:
        owner = AdminAccount(
            email=settings.initial_owner_email,
            password_hash=hash_password(settings.initial_owner_password),
            full_name="Владелец ИП (Снег-Диспетчер)",
            role=AdminRole.OWNER.value,
            is_active=True,
        )
        session.add(owner)
        await session.flush()

    # 4. Виртуальный чат бригады и демо-персонажи (при включённом симуляторе)
    if settings.enable_simulator:
        dispatch_chat = await session.get(BotChat, SIM_DISPATCH_CHAT_ID)
        if dispatch_chat is None:
            session.add(
                BotChat(
                    chat_id=SIM_DISPATCH_CHAT_ID,
                    title="Бригада «Снег-Диспетчер» (биржа заказов)",
                    chat_purpose=ChatPurpose.DISPATCH_POOL.value,
                    is_active=True,
                    bot_is_admin=True,
                )
            )

        for key, p in SIM_PERSONAS.items():
            user = (
                await session.execute(
                    select(User).where(User.max_user_id == p["max_user_id"])
                )
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    max_user_id=p["max_user_id"],
                    max_chat_id=p["max_chat_id"],
                    first_name=p["first_name"],
                    last_name=p["last_name"],
                    username=p["username"],
                    role=p["role"],
                    email=p.get("email"),
                )
                session.add(user)
                await session.flush()

                # Клиентам в симуляторе даём стартовые согласия, но клиент может также пройти /start
                if key.startswith("client_"):
                    session.add(
                        UserConsent(
                            user_id=user.id,
                            consent_type=ConsentType.PDN.value,
                            document_version="1.0",
                            source="simulator_seed",
                        )
                    )
                    session.add(
                        UserConsent(
                            user_id=user.id,
                            consent_type=ConsentType.CLIENT_OFFER.value,
                            document_version="1.0",
                            source="simulator_seed",
                        )
                    )
                else:
                    session.add(
                        UserConsent(
                            user_id=user.id,
                            consent_type=ConsentType.EXECUTOR_OFFER.value,
                            document_version="1.0",
                            source="simulator_seed",
                        )
                    )
                    session.add(
                        UserConsent(
                            user_id=user.id,
                            consent_type=ConsentType.SAFETY_BRIEFING.value,
                            document_version="1.0",
                            source="simulator_seed",
                        )
                    )
                    session.add(
                        ExecutorProfile(
                            user_id=user.id,
                            status=ExecutorStatus.ACTIVE.value,
                            is_self_employed=True,
                            inn=p["inn"],
                            max_active_orders=2 if key == "foreman_1" else 1,
                            rating=p["rating"],
                            ratings_count=12,
                            completed_orders_count=12,
                            has_snowblower=p["has_snowblower"],
                            has_miniloader=p["has_miniloader"],
                            has_tractor=p["has_tractor"],
                            roof_certified=p["roof_certified"],
                            insurance_policy_no="INS-2026-001" if p["roof_certified"] else None,
                            insurance_valid_until=(datetime.now(UTC) + timedelta(days=180)).date()
                            if p["roof_certified"]
                            else None,
                            verified_by=owner.id,
                            verified_at=datetime.now(UTC),
                        )
                    )

        # Связываем бригадира с учёткой админки для тестирования RBAC foreman
        foreman_admin = (
            await session.execute(
                select(AdminAccount).where(AdminAccount.email == "foreman@sneg.local")
            )
        ).scalar_one_or_none()
        if foreman_admin is None:
            foreman_user = (
                await session.execute(select(User).where(User.max_user_id == 30001))
            ).scalar_one_or_none()
            session.add(
                AdminAccount(
                    email="foreman@sneg.local",
                    password_hash=hash_password("foreman12345"),
                    full_name="Алексей Бригадир",
                    role=AdminRole.FOREMAN.value,
                    linked_user_id=foreman_user.id if foreman_user else None,
                    is_active=True,
                )
            )

    await session.commit()
