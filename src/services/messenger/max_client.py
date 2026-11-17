"""Боевой клиент MAX Bot API (platform-api2.max.ru, сверено по dev.max.ru на 11.2026)."""

from __future__ import annotations

import asyncio
import logging
import ssl
from pathlib import Path
from typing import Any

import httpx

from src.core.exceptions import ExternalIntegrationError
from src.services.messenger.base import (
    BotInfo,
    MessageTarget,
    OutgoingMessage,
    SentMessage,
    UploadedMedia,
)
from src.services.messenger.rate_limiter import InMemoryRateLimiter

logger = logging.getLogger(__name__)


class MaxApiClient:
    """Асинхронный клиент MAX Bot API.

    Особенности актуальной спецификации (dev.max.ru, 11.2026):
    - Домен: https://platform-api2.max.ru
    - Авторизация: строго заголовок `Authorization: <access_token>`
    - Поддержка сертификата Минцифры РФ через `ca_bundle_path`
    - Загрузка изображений: POST /uploads?type=image + retry на `attachment.not.ready`
    - Rate limiter: ≤25 RPS глобально и ≤2 req/sec на чат/диалог
    """

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://platform-api2.max.ru",
        ca_bundle_path: str = "",
        global_rps: int = 25,
        per_chat_rps: int = 2,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = InMemoryRateLimiter(
            global_rps=global_rps, per_chat_rps=per_chat_rps
        )
        self._owns_client = http_client is None
        if http_client is not None:
            self._client = http_client
        else:
            verify: bool | ssl.SSLContext = True
            if ca_bundle_path and Path(ca_bundle_path).exists():
                verify = ssl.create_default_context(cafile=ca_bundle_path)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(30.0, connect=10.0),
                verify=verify,
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.token,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        chat_key: str | None = None,
        retry_not_ready: bool = False,
    ) -> dict[str, Any]:
        delays = [0.5, 1.0, 2.0, 4.0] if retry_not_ready else []
        attempt = 0
        while True:
            await self.rate_limiter.acquire(chat_key)
            resp = await self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers=self._headers(),
            )
            data: dict[str, Any] = {}
            if resp.content:
                try:
                    data = resp.json()
                except ValueError:
                    data = {"raw": resp.text}

            if resp.status_code == 200:
                return data

            # Официальная обработка асинхронной обработки медиа в MAX
            if (
                retry_not_ready
                and data.get("code") == "attachment.not.ready"
                and attempt < len(delays)
            ):
                logger.warning("MAX attachment.not.ready, retry #%d", attempt + 1)
                await asyncio.sleep(delays[attempt])
                attempt += 1
                continue

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
                attempt += 1
                continue

            raise ExternalIntegrationError(
                f"MAX API error {resp.status_code}: {data}",
                details={"status_code": resp.status_code, "body": data},
            )

    async def get_me(self) -> BotInfo:
        data = await self._request("GET", "/me")
        return BotInfo(
            user_id=int(data["user_id"]),
            first_name=str(data.get("first_name", "")),
            username=data.get("username"),
        )

    async def send_message(self, target: MessageTarget, msg: OutgoingMessage) -> SentMessage:
        params: dict[str, Any] = {}
        chat_key: str | None = None
        if target.chat_id is not None:
            params["chat_id"] = target.chat_id
            chat_key = f"chat:{target.chat_id}"
        elif target.user_id is not None:
            params["user_id"] = target.user_id
            chat_key = f"user:{target.user_id}"
        else:
            raise ValueError("MessageTarget requires either chat_id or user_id")

        if msg.disable_link_preview:
            params["disable_link_preview"] = "true"

        body = msg.to_max_body()
        has_media = any(a.get("type") in ("image", "video", "file") for a in msg.extra_attachments)
        data = await self._request(
            "POST",
            "/messages",
            params=params,
            json_body=body,
            chat_key=chat_key,
            retry_not_ready=has_media,
        )
        msg_obj = data.get("message", data)
        body_obj = msg_obj.get("body", {}) if isinstance(msg_obj, dict) else {}
        mid = str(body_obj.get("mid") or msg_obj.get("mid") or "")
        recipient = msg_obj.get("recipient", {}) if isinstance(msg_obj, dict) else {}
        return SentMessage(
            message_id=mid,
            chat_id=recipient.get("chat_id") or target.chat_id,
            raw=data,
        )

    async def edit_message(
        self, message_id: str, msg: OutgoingMessage, *, clear_keyboard: bool = False
    ) -> bool:
        body = msg.to_max_body()
        if clear_keyboard and "attachments" not in body:
            body["attachments"] = []
        data = await self._request(
            "PUT",
            "/messages",
            params={"message_id": message_id},
            json_body=body,
            chat_key=f"edit:{message_id}",
        )
        return bool(data.get("success", True))

    async def answer_callback(
        self,
        callback_id: str,
        *,
        notification: str | None = None,
        message: OutgoingMessage | None = None,
        target_chat_id: int | None = None,
    ) -> bool:
        payload: dict[str, Any] = {}
        if notification:
            payload["notification"] = notification
        if message is not None:
            payload["message"] = message.to_max_body()
        if not payload:
            # MAX Bot API (POST /answers) строго требует message или notification
            payload["notification"] = "✓"
        try:
            data = await self._request(
                "POST",
                "/answers",
                params={"callback_id": callback_id},
                json_body=payload,
                chat_key=f"cb:{target_chat_id}" if target_chat_id else None,
            )
            return bool(data.get("success", True))
        except ExternalIntegrationError as exc:
            # Просроченный/повторный callback_id не должен ронять основной обработчик кнопки
            logger.warning(
                "MAX POST /answers non-fatal error for callback %s: %s", callback_id, exc
            )
            return False

    async def upload_image(self, data: bytes, filename: str) -> UploadedMedia:
        # Этап 1: получить одноразовый upload URL (type=image, type=photo удалён)
        init_resp = await self._request("POST", "/uploads", params={"type": "image"})
        upload_url = str(init_resp["url"])
        # Этап 2: multipart upload файла
        resp = await self._client.post(
            upload_url,
            headers={"Authorization": self.token},
            files={"data": (filename, data, "image/jpeg")},
        )
        if resp.status_code != 200:
            raise ExternalIntegrationError(f"MAX upload failed ({resp.status_code}): {resp.text}")
        res = resp.json()
        # MAX возвращает либо {"photos": {"...": {"token": "..."}}}, либо {"token": "..."}
        token = res.get("token")
        if not token and isinstance(res.get("photos"), dict):
            first_photo: dict[str, Any] = next(iter(res["photos"].values()), {})
            token = first_photo.get("token")
        if not token:
            token = init_resp.get("token", "")
        return UploadedMedia(token=str(token), url=upload_url)

    async def download_media(self, url: str) -> bytes:
        resp = await self._client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            raise ExternalIntegrationError(f"Не удалось скачать медиа MAX: {resp.status_code}")
        return resp.content

    async def list_webhooks(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/subscriptions")
        subs = data.get("subscriptions", [])
        return list(subs) if isinstance(subs, list) else []

    async def setup_webhook(
        self, *, url: str, secret: str, update_types: list[str]
    ) -> bool:
        data = await self._request(
            "POST",
            "/subscriptions",
            json_body={
                "url": url,
                "secret": secret,
                "update_types": update_types,
            },
        )
        return bool(data.get("success", True))

    async def delete_webhook(self, url: str) -> bool:
        data = await self._request("DELETE", "/subscriptions", params={"url": url})
        return bool(data.get("success", True))

    async def get_updates(
        self, *, marker: int | None = None, timeout: int = 30, limit: int = 100
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"timeout": timeout, "limit": limit}
        if marker is not None:
            params["marker"] = marker
        return await self._request("GET", "/updates", params=params)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
