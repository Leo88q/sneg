"""Фабрики inline-клавиатур MAX (с соблюдением лимита ≤3 спецкнопок в ряду)."""

from __future__ import annotations

from src.core.enums import UserRole
from src.infra.db.models import GeoZone
from src.services.messenger.base import InlineButton, InlineKeyboard


def onboarding_keyboard(offer_url: str, privacy_url: str) -> InlineKeyboard:
    return InlineKeyboard(
        rows=[
            [
                InlineButton(type="link", text="📄 Оферта", url=offer_url),
                InlineButton(type="link", text="🔒 Политика ПДн", url=privacy_url),
            ],
            [
                InlineButton(
                    type="callback",
                    text="✅ Принимаю условия",
                    payload="auth:accept_consents",
                )
            ],
        ]
    )


def main_menu_keyboard(role: str) -> InlineKeyboard:
    rows: list[list[InlineButton]] = [
        [InlineButton(type="callback", text="🚨 Срочно застрял", payload="svc:car_rescue")],
        [
            InlineButton(type="callback", text="❄️ Снег во дворе", payload="svc:snow_yard"),
            InlineButton(type="callback", text="🛣 Дорога / проезд", payload="svc:snow_road"),
        ],
        [
            InlineButton(type="callback", text="🧹 Помощь по двору", payload="svc:yard_help"),
            InlineButton(type="callback", text="📦 Грузчики", payload="svc:movers"),
        ],
        [
            InlineButton(type="callback", text="📋 Мои заказы", payload="menu:my_orders"),
            InlineButton(type="callback", text="❓ Помощь", payload="menu:help"),
        ],
    ]
    if role == UserRole.CLIENT.value:
        rows.append(
            [
                InlineButton(
                    type="callback", text="👷 Стать исполнителем", payload="exec:register"
                )
            ]
        )
    else:
        rows.append(
            [
                InlineButton(type="callback", text="📦 Заказы в работе", payload="exec:my_jobs"),
                InlineButton(type="callback", text="💰 Мой баланс", payload="exec:balance"),
            ]
        )
    if role in (UserRole.FOREMAN.value, UserRole.ADMIN.value, UserRole.OWNER.value):
        rows.append(
            [
                InlineButton(
                    type="callback", text="🛠 Панель бригадира", payload="foreman:panel"
                )
            ]
        )
    return InlineKeyboard(rows=rows)


def geo_zone_keyboard(zones: list[GeoZone]) -> InlineKeyboard:
    rows: list[list[InlineButton]] = [
        [
            InlineButton(
                type="request_geo_location",
                text="📍 Отправить геолокацию",
                quick=True,
            )
        ]
    ]
    row: list[InlineButton] = []
    for z in zones:
        if not z.is_active:
            continue
        row.append(
            InlineButton(
                type="callback",
                text=f"{z.name} (+{z.dispatch_fee:.0f} ₽)",
                payload=f"ord:zone:{z.code}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineButton(type="callback", text="🏠 Главное меню", payload="menu:home")])
    return InlineKeyboard(rows=rows)


def area_presets_keyboard() -> InlineKeyboard:
    return InlineKeyboard(
        rows=[
            [
                InlineButton(type="callback", text="50 м²", payload="ord:area:50"),
                InlineButton(type="callback", text="100 м²", payload="ord:area:100"),
                InlineButton(type="callback", text="120 м²", payload="ord:area:120"),
            ],
            [
                InlineButton(type="callback", text="200 м²", payload="ord:area:200"),
                InlineButton(type="callback", text="300 м²", payload="ord:area:300"),
                InlineButton(type="callback", text="500 м²", payload="ord:area:500"),
            ],
            [InlineButton(type="callback", text="🏠 Отмена", payload="menu:home")],
        ]
    )


def depth_keyboard() -> InlineKeyboard:
    return InlineKeyboard(
        rows=[
            [InlineButton(type="callback", text="До 10 см (×1.0)", payload="ord:depth:up_to_10")],
            [
                InlineButton(
                    type="callback", text="10–30 см (×1.5)", payload="ord:depth:from_10_to_30"
                )
            ],
            [
                InlineButton(
                    type="callback", text="Больше 30 см (×2.0)", payload="ord:depth:over_30"
                )
            ],
        ]
    )


def access_keyboard() -> InlineKeyboard:
    return InlineKeyboard(
        rows=[
            [
                InlineButton(
                    type="callback",
                    text="🚪 Узкая калитка (ручная / малая)",
                    payload="ord:access:narrow_gate",
                )
            ],
            [
                InlineButton(
                    type="callback",
                    text="🏡 Обычные ворота (мини-техника)",
                    payload="ord:access:standard",
                )
            ],
            [
                InlineButton(
                    type="callback",
                    text="🚜 Широкий проезд (пройдёт трактор)",
                    payload="ord:access:wide_road",
                )
            ],
        ]
    )


def urgency_keyboard() -> InlineKeyboard:
    return InlineKeyboard(
        rows=[
            [InlineButton(type="callback", text="🕐 В течение дня (×1.0)", payload="ord:urg:today")],
            [
                InlineButton(
                    type="callback", text="⚡ В течение 3 часов (×1.3)", payload="ord:urg:within_3h"
                )
            ],
            [
                InlineButton(
                    type="callback",
                    text="🚨 Прямо сейчас / Ночь (×1.5)",
                    payload="ord:urg:now_or_night",
                )
            ],
        ]
    )


def snow_removal_keyboard() -> InlineKeyboard:
    return InlineKeyboard(
        rows=[
            [
                InlineButton(type="callback", text="Нет, складировать", payload="ord:rem:no"),
                InlineButton(type="callback", text="🚛 Нужен вывоз", payload="ord:rem:yes"),
            ]
        ]
    )


def contact_mode_keyboard() -> InlineKeyboard:
    return InlineKeyboard(
        rows=[
            [
                InlineButton(
                    type="callback",
                    text="💬 Связь через чат бота (без номера)",
                    payload="ord:contact:bot_proxy",
                )
            ],
            [
                InlineButton(
                    type="callback",
                    text="📞 Указать номер для звонка бригады",
                    payload="ord:contact:direct_phone",
                )
            ],
        ]
    )


def vehicle_keyboard() -> InlineKeyboard:
    return InlineKeyboard(
        rows=[
            [
                InlineButton(type="callback", text="🚗 Легковая", payload="ord:veh:sedan"),
                InlineButton(type="callback", text="🚙 Кроссовер", payload="ord:veh:suv"),
            ],
            [InlineButton(type="callback", text="🚐 Коммерческая", payload="ord:veh:commercial")],
        ]
    )


def confirm_order_keyboard() -> InlineKeyboard:
    return InlineKeyboard(
        rows=[
            [
                InlineButton(
                    type="callback",
                    text="💳 Подтвердить и получить счёт СБП",
                    payload="ord:confirm",
                )
            ],
            [InlineButton(type="callback", text="❌ Отмена", payload="menu:home")],
        ]
    )
