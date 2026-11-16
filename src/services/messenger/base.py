"""Протокол MessengerTransport и валидация ограничений MAX Bot API (ТЗ B3.1, B8.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from src.core.exceptions import ValidationAppError

ButtonType = Literal[
    "callback",
    "link",
    "request_geo_location",
    "request_contact",
    "open_app",
    "message",
    "clipboard",
]
SPECIAL_ROW_LIMIT_TYPES = {"link", "open_app", "request_geo_location", "request_contact"}


@dataclass(slots=True)
class InlineButton:
    type: ButtonType
    text: str
    payload: str | None = None
    url: str | None = None
    quick: bool | None = None

    def to_max_dict(self) -> dict[str, Any]:
        if not self.text or len(self.text) > 128:
            raise ValidationAppError("Длина текста кнопки MAX должна быть 1..128 символов")
        data: dict[str, Any] = {"type": self.type, "text": self.text}
        if self.type == "callback":
            if not self.payload or len(self.payload) > 1024:
                raise ValidationAppError("callback.payload должен быть 1..1024 символов")
            data["payload"] = self.payload
        elif self.type == "link":
            if not self.url or len(self.url) > 2048:
                raise ValidationAppError("link.url должен быть 1..2048 символов")
            data["url"] = self.url
        elif self.type == "request_geo_location" and self.quick is not None:
            data["quick"] = self.quick
        elif self.type == "open_app" and self.url:
            data["url"] = self.url
        return data


@dataclass(slots=True)
class InlineKeyboard:
    rows: list[list[InlineButton]] = field(default_factory=list)

    def to_attachment(self) -> dict[str, Any]:
        validate_max_keyboard(self)
        return {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [[btn.to_max_dict() for btn in row] for row in self.rows],
            },
        }


def validate_max_keyboard(kb: InlineKeyboard) -> None:
    if len(kb.rows) > 30:
        raise ValidationAppError("В клавиатуре MAX допускается не более 30 рядов")
    total_buttons = 0
    for row in kb.rows:
        if not row:
            continue
        has_special = any(btn.type in SPECIAL_ROW_LIMIT_TYPES for btn in row)
        max_in_row = 3 if has_special else 7
        if len(row) > max_in_row:
            raise ValidationAppError(
                f"Превышен лимит кнопок в ряду MAX ({len(row)} > {max_in_row})"
            )
        for btn in row:
            btn.to_max_dict()
            total_buttons += 1
    if total_buttons > 210:
        raise ValidationAppError("В клавиатуре MAX допускается не более 210 кнопок")


@dataclass(slots=True)
class MessageTarget:
    user_id: int | None = None
    chat_id: int | None = None


@dataclass(slots=True)
class OutgoingMessage:
    text: str
    keyboard: InlineKeyboard | None = None
    extra_attachments: list[dict[str, Any]] = field(default_factory=list)
    format: Literal["markdown", "html"] | None = "markdown"
    notify: bool = True
    disable_link_preview: bool = False

    def to_max_body(self) -> dict[str, Any]:
        if len(self.text) > 4000:
            raise ValidationAppError("Текст сообщения MAX не может превышать 4000 символов")
        attachments: list[dict[str, Any]] = list(self.extra_attachments)
        if self.keyboard is not None:
            attachments.append(self.keyboard.to_attachment())
        body: dict[str, Any] = {"text": self.text, "notify": self.notify}
        if attachments:
            body["attachments"] = attachments
        elif self.keyboard is None and self.extra_attachments == []:
            # Для PUT /messages можно передать [] явно, чтобы удалить клавиатуру
            pass
        if self.format:
            body["format"] = self.format
        return body


@dataclass(slots=True)
class SentMessage:
    message_id: str
    chat_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BotInfo:
    user_id: int
    first_name: str
    username: str | None = None


@dataclass(slots=True)
class UploadedMedia:
    token: str
    url: str | None = None


class MessengerTransport(Protocol):
    async def get_me(self) -> BotInfo: ...

    async def send_message(self, target: MessageTarget, msg: OutgoingMessage) -> SentMessage: ...

    async def edit_message(
        self, message_id: str, msg: OutgoingMessage, *, clear_keyboard: bool = False
    ) -> bool: ...

    async def answer_callback(
        self,
        callback_id: str,
        *,
        notification: str | None = None,
        message: OutgoingMessage | None = None,
        target_chat_id: int | None = None,
    ) -> bool: ...

    async def upload_image(self, data: bytes, filename: str) -> UploadedMedia: ...

    async def download_media(self, url: str) -> bytes: ...

    async def setup_webhook(
        self, *, url: str, secret: str, update_types: list[str]
    ) -> bool: ...

    async def close(self) -> None: ...
