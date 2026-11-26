from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from src.core.enums import ChatPurpose, UserRole
from src.core.security import hash_password
from src.infra.db.models import AdminAccount, BotChat, User


def group_message(sender_id: int, chat_id: int, text: str, mid: str) -> dict[str, Any]:
    return {
        "update_type": "message_created",
        "message": {
            "sender": {"user_id": sender_id, "first_name": "Пользователь"},
            "recipient": {"chat_id": chat_id, "chat_type": "chat"},
            "body": {"text": text, "mid": mid},
        },
    }


@pytest.mark.asyncio
async def test_only_privileged_users_can_bind_group(client: Any) -> None:
    _, c = client
    await c.bot_router.handle_update(
        group_message(10001, 890001, "/bind_dispatch_chat", "bind-attacker")
    )
    async with c.session_factory() as session:
        assert await session.get(BotChat, 890001) is None
    # Seeded foreman is already an authorised member.
    await c.bot_router.handle_update(
        group_message(30001, 890001, "/bind_dispatch_chat", "bind-foreman")
    )
    async with c.session_factory() as session:
        group = await session.get(BotChat, 890001)
        assert group is not None and group.chat_purpose == ChatPurpose.DISPATCH_POOL.value
    await c.bot_router.handle_update(
        group_message(30001, 890001, "/bind_alerts_chat", "alerts-in-worker-group")
    )
    async with c.session_factory() as session:
        group = await session.get(BotChat, 890001)
        assert group is not None and group.chat_purpose == ChatPurpose.DISPATCH_POOL.value


@pytest.mark.asyncio
async def test_alert_binding_rejects_password_in_group_and_blocked_user(client: Any) -> None:
    _, c = client
    await c.bot_router.handle_update(
        group_message(10001, 890002, "/bind_alerts_chat admin12345", "group-password")
    )
    async with c.session_factory() as session:
        assert await session.get(BotChat, 890002) is None
        user = (await session.execute(select(User).where(User.max_user_id == 10001))).scalar_one()
        assert user.role == UserRole.CLIENT.value
        user.is_blocked = True
        await session.commit()
    await c.bot_router.handle_update(
        {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": 10001},
                "recipient": {"chat_id": 10001, "chat_type": "dialog"},
                "body": {"text": "/bind_alerts_chat admin12345", "mid": "blocked-password"},
            },
        }
    )
    async with c.session_factory() as session:
        user = (await session.execute(select(User).where(User.max_user_id == 10001))).scalar_one()
        assert user.role == UserRole.CLIENT.value
        assert await session.get(BotChat, 10001) is None


@pytest.mark.asyncio
async def test_admin_password_does_not_grant_owner(client: Any) -> None:
    _, c = client
    async with c.session_factory() as session:
        session.add(
            AdminAccount(
                email="admin2@example.test",
                password_hash=hash_password("special-admin-pass"),
                full_name="Admin",
                role="admin",
                is_active=True,
            )
        )
        await session.commit()
    await c.bot_router.handle_update(
        {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": 555004},
                "recipient": {"chat_id": 555004, "chat_type": "dialog"},
                "body": {"text": "/bind_alerts_chat special-admin-pass", "mid": "admin-bind"},
            },
        }
    )
    async with c.session_factory() as session:
        user = (await session.execute(select(User).where(User.max_user_id == 555004))).scalar_one()
        assert user.role == UserRole.ADMIN.value


@pytest.mark.asyncio
async def test_positive_group_id_does_not_replace_private_chat_id(client: Any) -> None:
    _, c = client
    await c.bot_router.handle_update(
        {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "group-123",
                "payload": "menu:home",
                "user": {"user_id": 10001},
            },
            "message": {"recipient": {"chat_id": 890099, "chat_type": "chat"}},
        }
    )
    async with c.session_factory() as session:
        user = (await session.execute(select(User).where(User.max_user_id == 10001))).scalar_one()
        assert user.max_chat_id == 10001
