"""API интерактивного веб-симулятора MAX-бота (/api/v1/simulator/* и WebSocket)."""

from __future__ import annotations

import asyncio
import io
import time
import uuid
from decimal import Decimal
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from PIL import Image, ImageDraw
from pydantic import BaseModel
from sqlalchemy import delete, select

from src.api.deps import get_container
from src.core.container import AppContainer
from src.core.enums import OrderStatus, PaymentStatus
from src.core.exceptions import AppError
from src.infra.db.models import Order, Payment, SimulatorMessage, User
from src.infra.db.seed import SIM_DISPATCH_CHAT_ID, SIM_PERSONAS
from src.services.messenger.simulator_transport import SimulatorTransport, _serialize_sim_message
from src.services.payments.mock import MockPaymentProvider
from src.services.weather.provider import MockWeatherProvider

router = APIRouter(prefix="/api/v1/simulator", tags=["simulator"])


def _ensure_enabled(container: AppContainer) -> SimulatorTransport:
    if not container.settings.enable_simulator:
        raise HTTPException(status_code=404, detail="Simulator disabled")
    if not isinstance(container.messenger, SimulatorTransport):
        raise HTTPException(status_code=400, detail="Messenger transport is not SimulatorTransport")
    return container.messenger


def _generate_demo_photo(preset: str) -> bytes:
    """Генерирует качественное тестовое JPEG-фото (Pillow) для быстрой проверки фотоотчётов."""
    palettes = {
        "car_stuck": ((30, 58, 95), (226, 232, 240), "CAR STUCK IN SNOW\nMalakhovka / Sneg-Dispatcher"),
        "before": ((51, 65, 85), (241, 245, 249), "PHOTO BEFORE (DO)\nDeep Snow 25 cm"),
        "after": ((20, 83, 45), (220, 252, 231), "PHOTO AFTER (POSLE)\nClean Yard & Path"),
    }
    bg, fg, label = palettes.get(preset, palettes["before"])
    img = Image.new("RGB", (640, 420), color=bg)
    draw = ImageDraw.Draw(img)
    # Рисуем схематичную сцену снега/двора
    if preset == "before":
        draw.rectangle([0, 230, 640, 420], fill=(226, 232, 240))
        draw.rectangle([60, 120, 260, 240], fill=(100, 116, 139))
        draw.polygon([(40, 120), (160, 40), (280, 120)], fill=(148, 163, 184))
    elif preset == "after":
        draw.rectangle([0, 230, 640, 420], fill=(71, 85, 105))
        draw.rectangle([240, 230, 400, 420], fill=(148, 163, 184))
        draw.rectangle([60, 120, 260, 240], fill=(100, 116, 139))
    else:
        draw.rectangle([0, 240, 640, 420], fill=(241, 245, 249))
        draw.rounded_rectangle([180, 180, 460, 280], radius=20, fill=(239, 68, 68))
        draw.ellipse([210, 255, 270, 315], fill=(15, 23, 42))
        draw.ellipse([370, 255, 430, 315], fill=(15, 23, 42))

    draw.rectangle([20, 20, 460, 92], fill=(15, 23, 42))
    draw.text((34, 34), label, fill=fg)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class SimStartBody(BaseModel):
    persona: str = "client_1"
    payload: str = ""


class SimSendMessageBody(BaseModel):
    persona: str = "client_1"
    chat_id: int | None = None
    text: str = ""
    location: dict[str, float] | None = None  # {"latitude": ..., "longitude": ...}
    contact_phone: str | None = None


class SimCallbackBody(BaseModel):
    persona: str = "client_1"
    chat_id: int | None = None
    payload: str


class SimPayLatestBody(BaseModel):
    result: Literal["succeeded", "canceled"] = "succeeded"


class SimWeatherBody(BaseModel):
    zone_code: str = "malakhovka"
    snow_cm: Decimal = Decimal("15")


@router.get("/state")
async def simulator_state(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    transport = _ensure_enabled(container)
    async with container.session_factory() as session:
        msgs = list(
            (
                await session.execute(
                    select(SimulatorMessage).order_by(SimulatorMessage.id.asc()).limit(500)
                )
            ).scalars().all()
        )
        pending_payments = list(
            (
                await session.execute(
                    select(Payment)
                    .where(Payment.status == PaymentStatus.PENDING.value)
                    .order_by(Payment.created_at.desc())
                )
            ).scalars().all()
        )
        recent_orders = list(
            (
                await session.execute(
                    select(Order).order_by(Order.created_at.desc()).limit(10)
                )
            ).scalars().all()
        )

    messages_by_chat: dict[str, list[dict[str, Any]]] = {}
    for m in msgs:
        key = str(m.chat_id)
        messages_by_chat.setdefault(key, []).append(_serialize_sim_message(m))

    return {
        "dispatch_chat_id": SIM_DISPATCH_CHAT_ID,
        "personas": {
            k: {
                "max_user_id": v["max_user_id"],
                "chat_id": v["max_chat_id"],
                "name": f"{v['first_name']} {v['last_name']}",
                "role": v["role"],
            }
            for k, v in SIM_PERSONAS.items()
        },
        "messages_by_chat": messages_by_chat,
        "pending_payments": [
            {
                "id": str(p.id),
                "order_id": str(p.order_id) if p.order_id else None,
                "purpose": p.purpose,
                "amount": str(p.amount),
                "confirmation_url": p.confirmation_url,
            }
            for p in pending_payments
        ],
        "recent_orders": [
            {
                "id": str(o.id),
                "public_number": o.public_number,
                "status": o.status,
                "final_total": str(o.final_total),
                "paid_amount": str(o.paid_amount),
            }
            for o in recent_orders
        ],
        "recent_notifications": transport.last_notifications[-10:],
    }


@router.post("/bot-start")
async def simulator_bot_start(
    body: SimStartBody,
    container: AppContainer = Depends(get_container),
) -> dict[str, bool]:
    _ensure_enabled(container)
    p = SIM_PERSONAS.get(body.persona)
    if not p:
        raise HTTPException(status_code=400, detail="Unknown persona")
    update = {
        "update_type": "bot_started",
        "timestamp": int(time.time() * 1000),
        "chat_id": p["max_chat_id"],
        "payload": body.payload,
        "user": {
            "user_id": p["max_user_id"],
            "first_name": p["first_name"],
            "last_name": p["last_name"],
            "username": p["username"],
            "is_bot": False,
        },
    }
    await container.bot_router.handle_update(update)
    return {"ok": True}


@router.post("/send-message")
async def simulator_send_message(
    body: SimSendMessageBody,
    container: AppContainer = Depends(get_container),
) -> dict[str, bool]:
    transport = _ensure_enabled(container)
    p = SIM_PERSONAS.get(body.persona)
    if not p:
        raise HTTPException(status_code=400, detail="Unknown persona")
    chat_id = body.chat_id if body.chat_id is not None else p["max_chat_id"]
    chat_type = "chat" if chat_id < 0 else "dialog"

    attachments: list[dict[str, Any]] = []
    display_text = body.text
    if body.location:
        attachments.append({"type": "location", "payload": body.location})
        if not display_text:
            display_text = (
                f"📍 Геолокация: {body.location['latitude']:.5f}, {body.location['longitude']:.5f}"
            )
    if body.contact_phone:
        attachments.append(
            {
                "type": "contact",
                "payload": {
                    "name": p["first_name"],
                    "vcf_info": f"BEGIN:VCARD\nTEL:{body.contact_phone}\nEND:VCARD",
                    "hash": "sim_verified_hash",
                },
            }
        )
        if not display_text:
            display_text = f"📱 Контакт MAX (request_contact): {body.contact_phone}"

    recorded = await transport.record_incoming_user_message(
        chat_id=chat_id,
        chat_type="group" if chat_id < 0 else "dialog",
        sender_role=p["role"],
        sender_max_user_id=p["max_user_id"],
        text=display_text,
        attachments=attachments,
    )

    update = {
        "update_type": "message_created",
        "timestamp": int(time.time() * 1000),
        "message": {
            "sender": {
                "user_id": p["max_user_id"],
                "first_name": p["first_name"],
                "last_name": p["last_name"],
                "username": p["username"],
                "is_bot": False,
            },
            "recipient": {"chat_id": chat_id, "chat_type": chat_type},
            "body": {
                "mid": recorded["max_message_id"],
                "text": body.text,
                "attachments": attachments,
            },
        },
    }
    await container.bot_router.handle_update(update)
    return {"ok": True}


@router.post("/upload-photo")
async def simulator_upload_photo(
    persona: str = Form("executor_1"),
    preset: str = Form("before"),
    caption: str = Form(""),
    file: UploadFile | None = File(default=None),
    container: AppContainer = Depends(get_container),
) -> dict[str, bool]:
    transport = _ensure_enabled(container)
    p = SIM_PERSONAS.get(persona)
    if not p:
        raise HTTPException(status_code=400, detail="Unknown persona")

    if file is not None:
        raw_bytes = await file.read()
        filename = file.filename or "upload.jpg"
        mime = file.content_type or "image/jpeg"
    else:
        raw_bytes = _generate_demo_photo(preset)
        filename = f"{preset}.jpg"
        mime = "image/jpeg"

    recorded = await transport.record_incoming_user_message(
        chat_id=p["max_chat_id"],
        chat_type="dialog",
        sender_role=p["role"],
        sender_max_user_id=p["max_user_id"],
        text=caption or f"📸 [Фото: {filename}]",
        attachments=[{"type": "image", "payload": {"token": f"sim:{filename}"}}],
    )

    update = {
        "update_type": "message_created",
        "timestamp": int(time.time() * 1000),
        "message": {
            "sender": {
                "user_id": p["max_user_id"],
                "first_name": p["first_name"],
                "last_name": p["last_name"],
                "username": p["username"],
                "is_bot": False,
            },
            "recipient": {"chat_id": p["max_chat_id"], "chat_type": "dialog"},
            "body": {
                "mid": recorded["max_message_id"],
                "text": caption,
                "attachments": [{"type": "image", "payload": {"token": f"sim:{filename}"}}],
            },
        },
    }
    await container.bot_router.handle_update(
        update, uploaded_bytes=raw_bytes, uploaded_filename=filename, uploaded_mime=mime
    )
    return {"ok": True}


@router.post("/callback")
async def simulator_callback(
    body: SimCallbackBody,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    transport = _ensure_enabled(container)
    p = SIM_PERSONAS.get(body.persona)
    if not p:
        raise HTTPException(status_code=400, detail="Unknown persona")
    chat_id = body.chat_id if body.chat_id is not None else p["max_chat_id"]
    cb_id = f"sim.cb.{uuid.uuid4().hex[:12]}"

    update = {
        "update_type": "message_callback",
        "timestamp": int(time.time() * 1000),
        "callback": {
            "callback_id": cb_id,
            "payload": body.payload,
            "user": {
                "user_id": p["max_user_id"],
                "first_name": p["first_name"],
                "last_name": p["last_name"],
                "username": p["username"],
                "is_bot": False,
            },
        },
        "message": {
            "recipient": {
                "chat_id": chat_id,
                "chat_type": "chat" if chat_id < 0 else "dialog",
            }
        },
    }
    before_len = len(transport.last_notifications)
    await container.bot_router.handle_update(update)
    new_notes = transport.last_notifications[before_len:]
    return {"ok": True, "notifications": new_notes}


@router.post("/race-take")
async def simulator_race_take(
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Эмулирует одновременное нажатие «Беру» двумя исполнителями (Иван vs Сергей)."""
    _ensure_enabled(container)
    async with container.session_factory() as session:
        order = (
            await session.execute(
                select(Order)
                .where(Order.status == OrderStatus.SEARCHING_EXECUTOR.value)
                .order_by(Order.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=400,
                detail="Нет заказов в статусе searching_executor для проверки гонки «Беру»",
            )
        u1 = (
            await session.execute(
                select(User).where(User.max_user_id == SIM_PERSONAS["executor_1"]["max_user_id"])
            )
        ).scalar_one()
        u2 = (
            await session.execute(
                select(User).where(User.max_user_id == SIM_PERSONAS["executor_2"]["max_user_id"])
            )
        ).scalar_one()

    async def _try_take(user: User) -> dict[str, Any]:
        try:
            o, _ = await container.orchestrator.take_order_atomic(
                order_id=order.id, executor_user_id=user.id
            )
            return {"executor": user.display_name, "won": True, "message": f"Забрал заказ #{o.public_number}!"}
        except AppError as exc:
            return {"executor": user.display_name, "won": False, "message": exc.message}

    results = await asyncio.gather(_try_take(u1), _try_take(u2))
    return {"order_public_number": order.public_number, "results": list(results)}


@router.post("/trigger/pay-latest")
async def simulator_pay_latest(
    body: SimPayLatestBody,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    _ensure_enabled(container)
    async with container.session_factory() as session:
        pay = (
            await session.execute(
                select(Payment)
                .where(Payment.status == PaymentStatus.PENDING.value)
                .order_by(Payment.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if pay is None:
        raise HTTPException(status_code=400, detail="Нет ожидающих оплаты счетов")
    if isinstance(container.payment_provider, MockPaymentProvider) and pay.provider_payment_id:
        container.payment_provider.mark_status(pay.provider_payment_id, body.result)
    if body.result == "succeeded":
        p, o = await container.orchestrator.confirm_payment_succeeded(pay.id)
        return {
            "payment_id": str(p.id),
            "amount": str(p.amount),
            "purpose": p.purpose,
            "order_public_number": o.public_number if o else None,
            "order_status": o.status if o else None,
        }
    p = await container.orchestrator.cancel_payment(pay.id)
    return {"payment_id": str(p.id), "status": p.status}


@router.post("/trigger/timers")
async def simulator_run_timers(
    container: AppContainer = Depends(get_container),
) -> dict[str, int]:
    _ensure_enabled(container)
    return await container.orchestrator.run_expired_timers(force_all=True)


@router.post("/trigger/weather")
async def simulator_trigger_weather(
    body: SimWeatherBody,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    _ensure_enabled(container)
    if isinstance(container.weather_provider, MockWeatherProvider):
        container.weather_provider.set_zone_snowfall(body.zone_code, body.snow_cm)
    notified = await container.orchestrator.trigger_weather_snowfall(
        zone_code=body.zone_code, snow_cm=body.snow_cm
    )
    return {"zone_code": body.zone_code, "snow_cm": str(body.snow_cm), "notified": notified}


@router.post("/reset")
async def simulator_reset(container: AppContainer = Depends(get_container)) -> dict[str, bool]:
    transport = _ensure_enabled(container)
    async with container.session_factory() as session:
        await session.execute(delete(SimulatorMessage))
        await session.commit()
    for p in SIM_PERSONAS.values():
        await container.fsm.clear(p["max_user_id"])
    transport.last_notifications.clear()
    await transport._broadcast({"type": "reset"})
    return {"ok": True}


@router.websocket("/ws")
async def simulator_ws(websocket: WebSocket) -> None:
    container: AppContainer = websocket.app.state.container
    if not container.settings.enable_simulator or not isinstance(
        container.messenger, SimulatorTransport
    ):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue = container.messenger.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        container.messenger.unsubscribe(queue)
