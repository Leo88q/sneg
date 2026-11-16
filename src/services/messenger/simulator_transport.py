"""Локальный транспорт для интерактивного веб-симулятора MAX-бота."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import SimulatorMessage, utcnow
from src.services.messenger.base import (
    BotInfo,
    MessageTarget,
    OutgoingMessage,
    SentMessage,
    UploadedMedia,
)


class SimulatorTransport:
    """Хранит сообщения в БД и рассылает live-события в WebSocket веб-симулятора."""

    BOT_MAX_USER_ID = 999999

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.last_notifications: list[dict[str, Any]] = []

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    async def _broadcast(self, event: dict[str, Any]) -> None:
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)

    async def record_incoming_user_message(
        self,
        *,
        chat_id: int,
        chat_type: str,
        sender_role: str,
        sender_max_user_id: int,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        mid = f"sim.u.{uuid.uuid4().hex[:12]}"
        async with self.session_factory() as session:
            row = SimulatorMessage(
                chat_id=chat_id,
                chat_type=chat_type,
                sender_role=sender_role,
                sender_max_user_id=sender_max_user_id,
                max_message_id=mid,
                text=text,
                attachments_json=attachments or [],
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            serialized = _serialize_sim_message(row)
        await self._broadcast({"type": "message_created", "message": serialized})
        return serialized

    async def get_me(self) -> BotInfo:
        return BotInfo(
            user_id=self.BOT_MAX_USER_ID,
            first_name="Снег-Диспетчер (Симулятор)",
            username="sneg_dispatcher_bot",
        )

    async def send_message(self, target: MessageTarget, msg: OutgoingMessage) -> SentMessage:
        chat_id = target.chat_id if target.chat_id is not None else target.user_id
        if chat_id is None:
            raise ValueError("Target chat_id or user_id is required")
        chat_type = "group" if chat_id < 0 else "dialog"
        body = msg.to_max_body()
        mid = f"sim.b.{uuid.uuid4().hex[:12]}"

        async with self.session_factory() as session:
            row = SimulatorMessage(
                chat_id=chat_id,
                chat_type=chat_type,
                sender_role="bot",
                sender_max_user_id=self.BOT_MAX_USER_ID,
                max_message_id=mid,
                text=body.get("text", ""),
                attachments_json=body.get("attachments", []),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            serialized = _serialize_sim_message(row)

        await self._broadcast({"type": "message_created", "message": serialized})
        return SentMessage(message_id=mid, chat_id=chat_id, raw=serialized)

    async def edit_message(
        self, message_id: str, msg: OutgoingMessage, *, clear_keyboard: bool = False
    ) -> bool:
        body = msg.to_max_body()
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(SimulatorMessage).where(SimulatorMessage.max_message_id == message_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            row.text = body.get("text", row.text)
            if "attachments" in body:
                row.attachments_json = body["attachments"]
            elif clear_keyboard:
                row.attachments_json = [
                    a for a in row.attachments_json if a.get("type") != "inline_keyboard"
                ]
            row.is_edited = True
            row.updated_at = utcnow()
            await session.commit()
            await session.refresh(row)
            serialized = _serialize_sim_message(row)

        await self._broadcast({"type": "message_edited", "message": serialized})
        return True

    async def answer_callback(
        self,
        callback_id: str,
        *,
        notification: str | None = None,
        message: OutgoingMessage | None = None,
        target_chat_id: int | None = None,
    ) -> bool:
        if notification:
            note = {
                "callback_id": callback_id,
                "chat_id": target_chat_id,
                "notification": notification,
            }
            self.last_notifications.append(note)
            if len(self.last_notifications) > 50:
                self.last_notifications = self.last_notifications[-50:]
            await self._broadcast({"type": "callback_notification", **note})
        if message is not None and target_chat_id is not None:
            await self.send_message(MessageTarget(chat_id=target_chat_id), message)
        return True

    async def upload_image(self, data: bytes, filename: str) -> UploadedMedia:
        return UploadedMedia(token=f"sim-img-{uuid.uuid4().hex[:10]}")

    async def download_media(self, url: str) -> bytes:
        # В симуляторе фото передаются напрямую через LocalFileStorage
        return b""

    async def setup_webhook(
        self, *, url: str, secret: str, update_types: list[str]
    ) -> bool:
        return True

    async def close(self) -> None:
        self._subscribers.clear()


def _serialize_sim_message(row: SimulatorMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "chat_id": row.chat_id,
        "chat_type": row.chat_type,
        "sender_role": row.sender_role,
        "sender_max_user_id": row.sender_max_user_id,
        "max_message_id": row.max_message_id,
        "text": row.text,
        "attachments": row.attachments_json,
        "is_edited": row.is_edited,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
