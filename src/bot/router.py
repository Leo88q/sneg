"""Единый маршрутизатор MAX Update (идентичен для MAX API и веб-симулятора)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.fsm import FSMStorage
from src.bot.keyboards import (
    access_keyboard,
    area_presets_keyboard,
    confirm_order_keyboard,
    contact_mode_keyboard,
    depth_keyboard,
    geo_zone_keyboard,
    main_menu_keyboard,
    onboarding_keyboard,
    snow_removal_keyboard,
    urgency_keyboard,
    vehicle_keyboard,
)
from src.core.config import Settings
from src.core.enums import (
    METHOD_LABELS,
    SERVICE_LABELS,
    STATUS_LABELS,
    AccessConstraint,
    ChatPurpose,
    ConsentType,
    ContactMode,
    DepthBucket,
    DisputeStatus,
    ExecutorStatus,
    LedgerEntryStatus,
    MediaStage,
    OrderStatus,
    ServiceType,
    UrgencyLevel,
    UserRole,
)
from src.core.exceptions import AppError
from src.domain.geo.service import GeoZoneCandidate, resolve_geo_zone_by_coords
from src.domain.pricing.calculator import (
    ZERO,
    PriceCalculationInput,
    calculate_price,
    to_decimal,
)
from src.infra.db.models import (
    BotChat,
    DataSubjectRequest,
    Dispute,
    ExecutorProfile,
    GeoZone,
    Order,
    OrderAssignment,
    PayoutLedgerEntry,
    ProcessedWebhookEvent,
    User,
    UserConsent,
)
from src.services.messenger.base import (
    InlineButton,
    InlineKeyboard,
    MessageTarget,
    MessengerTransport,
    OutgoingMessage,
)
from src.services.orders.orchestrator import ACTIVE_ASSIGNMENT_STATUSES, OrderOrchestrator

logger = logging.getLogger(__name__)

DEPTH_LABELS = {
    DepthBucket.UP_TO_10.value: "до 10 см",
    DepthBucket.FROM_10_TO_30.value: "10–30 см",
    DepthBucket.OVER_30.value: "больше 30 см",
}


class BotRouter:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        fsm: FSMStorage,
        messenger: MessengerTransport,
        orchestrator: OrderOrchestrator,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.fsm = fsm
        self.messenger = messenger
        self.orchestrator = orchestrator

    async def _check_idempotency(self, update: dict[str, Any]) -> bool:
        """Возвращает True, если событие новое; False — если дубль."""
        # Формируем стабильный ключ события (callback_id / mid / hash)
        utype = str(update.get("update_type", "unknown"))
        if utype == "message_callback":
            cb_id = update.get("callback", {}).get("callback_id")
            event_key = f"cb:{cb_id}" if cb_id else ""
        elif utype == "message_created":
            mid = update.get("message", {}).get("body", {}).get("mid")
            event_key = f"msg:{mid}" if mid else ""
        else:
            ts = update.get("timestamp", "")
            uid = update.get("user", {}).get("user_id", "")
            cid = update.get("chat_id", "")
            event_key = f"{utype}:{cid}:{uid}:{ts}"

        if not event_key:
            digest = hashlib.sha256(
                json.dumps(update, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            event_key = f"hash:{digest}"

        async with self.session_factory() as session:
            session.add(ProcessedWebhookEvent(source="max", event_key=event_key))
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def _get_or_create_user(
        self, session: AsyncSession, raw_user: dict[str, Any], chat_id: int | None = None
    ) -> User:
        max_uid = int(raw_user["user_id"])
        user = (
            await session.execute(select(User).where(User.max_user_id == max_uid))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                max_user_id=max_uid,
                max_chat_id=chat_id if (chat_id and chat_id > 0) else max_uid,
                first_name=str(raw_user.get("first_name") or raw_user.get("name") or "Клиент"),
                last_name=raw_user.get("last_name"),
                username=raw_user.get("username"),
                role=UserRole.CLIENT.value,
            )
            session.add(user)
            await session.flush()
        elif chat_id and chat_id > 0 and user.max_chat_id != chat_id:
            user.max_chat_id = chat_id
        return user

    async def _has_client_consents(self, session: AsyncSession, user_id: uuid.UUID) -> bool:
        cnt = (
            await session.execute(
                select(func.count(UserConsent.id)).where(
                    UserConsent.user_id == user_id,
                    UserConsent.consent_type.in_(
                        [ConsentType.PDN.value, ConsentType.CLIENT_OFFER.value]
                    ),
                )
            )
        ).scalar() or 0
        return cnt >= 2

    async def handle_update(
        self,
        update: dict[str, Any],
        *,
        uploaded_bytes: bytes | None = None,
        uploaded_filename: str | None = None,
        uploaded_mime: str = "image/jpeg",
    ) -> None:
        if not await self._check_idempotency(update):
            logger.info("Duplicate MAX update ignored: %s", update.get("update_type"))
            return

        utype = str(update.get("update_type", ""))
        if utype == "bot_added":
            await self._on_bot_added(update)
        elif utype == "bot_removed":
            await self._on_bot_removed(update)
        elif utype == "bot_started":
            await self._on_bot_started(update)
        elif utype == "message_created":
            await self._on_message_created(
                update,
                uploaded_bytes=uploaded_bytes,
                uploaded_filename=uploaded_filename,
                uploaded_mime=uploaded_mime,
            )
        elif utype == "message_callback":
            await self._on_message_callback(update)

    async def _on_bot_added(self, update: dict[str, Any]) -> None:
        chat_id = update.get("chat_id")
        if chat_id is None:
            return
        async with self.session_factory() as session:
            existing = await session.get(BotChat, int(chat_id))
            if existing is None:
                session.add(
                    BotChat(
                        chat_id=int(chat_id),
                        title=f"Групповой чат {chat_id}",
                        chat_purpose=ChatPurpose.OTHER.value,
                        is_active=True,
                        bot_is_admin=True,
                    )
                )
            else:
                existing.is_active = True
            await session.commit()

    async def _on_bot_removed(self, update: dict[str, Any]) -> None:
        chat_id = update.get("chat_id")
        if chat_id is None:
            return
        async with self.session_factory() as session:
            existing = await session.get(BotChat, int(chat_id))
            if existing:
                existing.is_active = False
                await session.commit()

    async def _on_bot_started(self, update: dict[str, Any]) -> None:
        raw_user = update.get("user")
        if not raw_user:
            return
        chat_id = update.get("chat_id")
        payload = update.get("payload") or ""
        async with self.session_factory() as session:
            user = await self._get_or_create_user(session, raw_user, chat_id)
            has_consents = await self._has_client_consents(session, user.id)
            await session.commit()

        await self.fsm.clear(user.max_user_id)
        if not has_consents and user.role == UserRole.CLIENT.value:
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=(
                        "❄️ **Добро пожаловать в «Снег-Диспетчер»!**\n"
                        "Уборка снега, расчистка дорог, срочная помощь авто в сугробе и бытовые услуги "
                        "в Малаховке, Томилино, Красково, Быково и Лыткарино.\n\n"
                        "Для продолжения ознакомьтесь с публичной офертой и политикой обработки ПДн:"
                    ),
                    keyboard=onboarding_keyboard(
                        self.settings.public_offer_url, self.settings.privacy_policy_url
                    ),
                ),
            )
            return

        if payload == "urgent_car":
            await self._start_service_flow(user, ServiceType.CAR_RESCUE)
            return
        await self._send_main_menu(user)

    async def _send_main_menu(self, user: User, prefix: str = "") -> None:
        greeting = prefix or f"👋 **{user.display_name}**, выберите услугу или раздел:"
        await self.messenger.send_message(
            MessageTarget(user_id=user.max_user_id),
            OutgoingMessage(text=greeting, keyboard=main_menu_keyboard(user.role)),
        )

    async def _on_message_created(
        self,
        update: dict[str, Any],
        *,
        uploaded_bytes: bytes | None = None,
        uploaded_filename: str | None = None,
        uploaded_mime: str = "image/jpeg",
    ) -> None:
        msg = update.get("message", {})
        sender = msg.get("sender")
        recipient = msg.get("recipient", {})
        body = msg.get("body", {})
        if not sender or sender.get("is_bot"):
            return

        chat_type = recipient.get("chat_type", "dialog")
        chat_id = recipient.get("chat_id")
        text = str(body.get("text") or "").strip()
        attachments = list(body.get("attachments") or [])

        # Групповой чат: поддерживаем команду привязки /bind_dispatch_chat
        if chat_type == "chat" and chat_id is not None:
            if text.startswith("/bind_dispatch_chat"):
                async with self.session_factory() as session:
                    chat = await session.get(BotChat, int(chat_id))
                    if chat is None:
                        chat = BotChat(
                            chat_id=int(chat_id),
                            title="Общий чат бригады",
                            chat_purpose=ChatPurpose.DISPATCH_POOL.value,
                            is_active=True,
                            bot_is_admin=True,
                        )
                        session.add(chat)
                    else:
                        chat.chat_purpose = ChatPurpose.DISPATCH_POOL.value
                        chat.is_active = True
                    await session.commit()
                await self.messenger.send_message(
                    MessageTarget(chat_id=int(chat_id)),
                    OutgoingMessage(
                        text="✅ Этот чат привязан как **основная биржа заказов бригады**."
                    ),
                )
            return

        async with self.session_factory() as session:
            user = await self._get_or_create_user(session, sender, chat_id)
            if user.is_blocked:
                await session.commit()
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="⛔ Ваш аккаунт заблокирован. Обратитесь в поддержку."),
                )
                return

            # Обработка request_contact (СТРОГО: сохраняем только в bot_phone, НЕ передаём исполнителю!)
            for att in attachments:
                if att.get("type") == "contact":
                    vcf = str(att.get("payload", {}).get("vcf_info") or "")
                    phone_match = re.search(r"(\+?\d[\d\s\-()]{8,})", vcf)
                    if phone_match:
                        user.bot_phone = phone_match.group(1).strip()
                        await session.commit()
                        await self.messenger.send_message(
                            MessageTarget(user_id=user.max_user_id),
                            OutgoingMessage(
                                text="✅ Контакт подтверждён для работы внутри бота (исполнителю он не передаётся)."
                            ),
                        )
                        return
            await session.commit()

        if text in ("/start", "/menu", "Меню"):
            await self._on_bot_started({"user": sender, "chat_id": chat_id, "payload": ""})
            return

        if text in ("/privacy", "/legal", "Правовая информация"):
            base = self.settings.public_base_url.rstrip("/") or "https://chistodvor.ru"
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=(
                        f"⚖️ **Правовая информация и защита ПДн ({self.settings.brand_name})**\n\n"
                        f"• Политика ПДн (152-ФЗ): {base}/legal/privacy\n"
                        f"• Согласие на ПДн: {base}/legal/consent\n"
                        f"• Публичная оферта: {base}/legal/offer\n"
                        f"• Условия возврата (СБП): {base}/legal/refunds\n"
                        f"• Реквизиты и РКН: {base}/legal/requisites\n"
                        f"• Удаление данных на сайте: {base}/legal/my-data\n\n"
                        "Команды прямо в боте:\n"
                        "• `/unsubscribe` — мгновенная отписка от рассылок\n"
                        "• `/delete_my_data` — подать запрос на удаление моих ПДн"
                    )
                ),
            )
            return

        if text == "/unsubscribe":
            now = datetime.now(UTC)
            ticket_code = f"DSR-{now.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
            async with self.session_factory() as session:
                session.add(
                    UserConsent(
                        user_id=user.id,
                        consent_type="marketing_revoked",
                        document_version=self.settings.legal_doc_version,
                        source="max_bot",
                    )
                )
                session.add(
                    DataSubjectRequest(
                        ticket_code=ticket_code,
                        request_type="unsubscribe_marketing",
                        status="completed",
                        contact_value=f"max:{user.max_user_id}",
                        max_user_id=user.max_user_id,
                        user_id=user.id,
                        consent_accepted_at=now,
                        age_confirmed=True,
                        source="max_bot",
                        resolution_note="Отписка от рассылок через команду /unsubscribe в MAX",
                        resolved_at=now,
                    )
                )
                await session.commit()
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=(
                        f"✅ **Вы отписаны от информационных и рекламных рассылок** "
                        f"(обращение `{ticket_code}`).\n"
                        "Сервисные уведомления по активным заказам сохраняются."
                    )
                ),
            )
            return

        if text == "/delete_my_data":
            now = datetime.now(UTC)
            ticket_code = f"DSR-{now.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
            async with self.session_factory() as session:
                session.add(
                    DataSubjectRequest(
                        ticket_code=ticket_code,
                        request_type="delete_data",
                        status="pending",
                        contact_value=f"max:{user.max_user_id}",
                        max_user_id=user.max_user_id,
                        user_id=user.id,
                        details="Запрос на удаление ПДн подан через команду /delete_my_data в MAX",
                        consent_accepted_at=now,
                        age_confirmed=True,
                        source="max_bot",
                    )
                )
                await session.commit()
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=(
                        f"🛡 **Запрос на удаление ваших персональных данных зарегистрирован** "
                        f"(номер `{ticket_code}`).\n\n"
                        "Срок исполнения по ст. 21 152-ФЗ — **до 10 рабочих дней**. "
                        "Данные профиля будут удалены/обезличены (за исключением фискальных записей "
                        "54-ФЗ, хранение которых обязательно по закону РФ)."
                    )
                ),
            )
            return

        rec = await self.fsm.get(user.max_user_id)
        if rec is None:
            await self._send_main_menu(user)
            return

        try:
            await self._dispatch_fsm_message(
                user=user,
                state=rec.state,
                data=rec.data,
                text=text,
                attachments=attachments,
                uploaded_bytes=uploaded_bytes,
                uploaded_filename=uploaded_filename,
                uploaded_mime=uploaded_mime,
            )
        except AppError as exc:
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(text=f"⚠️ {exc.message}"),
            )

    async def _on_message_callback(self, update: dict[str, Any]) -> None:
        cb = update.get("callback", {})
        cb_id = str(cb.get("callback_id", ""))
        payload = str(cb.get("payload", ""))
        raw_user = cb.get("user")
        msg = update.get("message", {})
        chat_id = msg.get("recipient", {}).get("chat_id")
        if not raw_user:
            return

        async with self.session_factory() as session:
            user = await self._get_or_create_user(session, raw_user, chat_id)
            await session.commit()

        if user.is_blocked:
            await self.messenger.answer_callback(
                cb_id, notification="Аккаунт заблокирован", target_chat_id=chat_id
            )
            return

        parts = payload.split(":")
        ns = parts[0] if parts else ""
        action = parts[1] if len(parts) > 1 else ""

        try:
            if ns == "auth" and action == "accept_consents":
                async with self.session_factory() as session:
                    session.add(
                        UserConsent(
                            user_id=user.id,
                            consent_type=ConsentType.PDN.value,
                            document_version="1.0",
                        )
                    )
                    session.add(
                        UserConsent(
                            user_id=user.id,
                            consent_type=ConsentType.CLIENT_OFFER.value,
                            document_version="1.0",
                        )
                    )
                    await session.commit()
                await self.messenger.answer_callback(
                    cb_id, notification="Согласия приняты!", target_chat_id=user.max_user_id
                )
                await self._send_main_menu(user, prefix="✅ Условия приняты! Выберите услугу:")
                return

            if ns == "menu":
                await self.messenger.answer_callback(cb_id, target_chat_id=user.max_user_id)
                if action == "home":
                    await self.fsm.clear(user.max_user_id)
                    await self._send_main_menu(user)
                elif action == "my_orders":
                    await self._show_client_orders(user)
                elif action == "help":
                    await self.messenger.send_message(
                        MessageTarget(user_id=user.max_user_id),
                        OutgoingMessage(
                            text=(
                                "❓ **Как работает «Снег-Диспетчер»:**\n"
                                "1. Выбираете услугу и параметры — получаете ориентировочную цену.\n"
                                "2. Оплачиваете аванс 30–50% по СБП — заказ уходит в бригаду.\n"
                                "3. Исполнитель присылает фото «ДО» и «ПОСЛЕ» работы.\n"
                                "4. Принимаете работу и оплачиваете остаток.\n\n"
                                "Если на месте объём отличается более чем на 20%, доплата только с вашего согласия."
                            ),
                            keyboard=InlineKeyboard(
                                rows=[
                                    [
                                        InlineButton(
                                            type="callback",
                                            text="🏠 Главное меню",
                                            payload="menu:home",
                                        )
                                    ]
                                ]
                            ),
                        ),
                    )
                return

            if ns == "svc":
                await self.messenger.answer_callback(cb_id, target_chat_id=user.max_user_id)
                await self._start_service_flow(user, ServiceType(action))
                return

            if ns == "ord":
                await self._handle_order_callback(user, cb_id, chat_id, parts)
                return

            if ns == "acc":
                order_id = uuid.UUID(parts[2])
                if action == "ok":
                    await self.messenger.answer_callback(
                        cb_id, notification="Работа принята!", target_chat_id=user.max_user_id
                    )
                    await self.orchestrator.accept_order_by_client(
                        order_id=order_id, client_user_id=user.id
                    )
                elif action == "disp":
                    await self.messenger.answer_callback(cb_id, target_chat_id=user.max_user_id)
                    await self.fsm.set(
                        user.max_user_id, "dispute:reason", {"order_id": str(order_id)}
                    )
                    await self.messenger.send_message(
                        MessageTarget(user_id=user.max_user_id),
                        OutgoingMessage(
                            text="⚠️ Опишите, пожалуйста, ваши замечания по качеству работы (текстом):"
                        ),
                    )
                return

            if ns == "adj":
                adj_id = uuid.UUID(parts[2])
                decision_map = {"ok": "approve", "keep": "keep_old", "cancel": "cancel"}
                decision = decision_map.get(action, "keep_old")
                await self.orchestrator.respond_price_adjustment(
                    adjustment_id=adj_id, client_user_id=user.id, decision=decision
                )
                await self.messenger.answer_callback(
                    cb_id, notification="Ваш ответ записан!", target_chat_id=user.max_user_id
                )
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="✅ Ваш ответ по корректировке цены передан бригаде."),
                )
                return

            if ns == "exec":
                await self._handle_executor_callback(user, cb_id, parts)
                return

            if ns == "foreman":
                await self._handle_foreman_callback(user, cb_id, parts)
                return

            if ns == "disp" and action == "rework":
                dispute_id = uuid.UUID(parts[2])
                await self.orchestrator.resolve_dispute(
                    dispute_id=dispute_id,
                    resolution=DisputeStatus.RESOLVED_REWORK,
                    note="Возвращено на доработку бригадиром",
                    actor_user_id=user.id,
                )
                await self.messenger.answer_callback(
                    cb_id, notification="Отправлено на доработку", target_chat_id=user.max_user_id
                )
                return

            if ns == "sub" and action == "go":
                choice = parts[3] if len(parts) > 3 else "yes"
                note = "Выезд по подписке подтверждён!" if choice == "yes" else "Выезд пропущен."
                await self.messenger.answer_callback(
                    cb_id, notification=note, target_chat_id=user.max_user_id
                )
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text=f"📅 {note}"),
                )
                return

        except AppError as exc:
            await self.messenger.answer_callback(
                cb_id, notification=exc.message[:180], target_chat_id=chat_id or user.max_user_id
            )
            if not chat_id or chat_id > 0:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text=f"⚠️ {exc.message}"),
                )

    async def _start_service_flow(self, user: User, service_type: ServiceType) -> None:
        async with self.session_factory() as session:
            zones = list(
                (
                    await session.execute(
                        select(GeoZone).where(GeoZone.is_active.is_(True)).order_by(GeoZone.tier)
                    )
                ).scalars().all()
            )
        await self.fsm.set(
            user.max_user_id,
            "order:geo",
            {"service_type": service_type.value},
        )
        if service_type == ServiceType.CAR_RESCUE:
            txt = (
                "🚨 **Срочная помощь — машина в сугробе (4 быстрых шага)**\n"
                "Шаг 1/4: Отправьте геолокацию или выберите район, где вы застряли:"
            )
        else:
            txt = (
                f"🛠 **{SERVICE_LABELS[service_type]}**\n"
                "Шаг 1: Отправьте геолокацию или выберите ваш населённый пункт:"
            )
        await self.messenger.send_message(
            MessageTarget(user_id=user.max_user_id),
            OutgoingMessage(text=txt, keyboard=geo_zone_keyboard(zones)),
        )

    async def _handle_order_callback(
        self, user: User, cb_id: str, chat_id: int | None, parts: list[str]
    ) -> None:
        action = parts[1] if len(parts) > 1 else ""

        if action == "take":
            order_id = uuid.UUID(parts[2])
            order, _ = await self.orchestrator.take_order_atomic(
                order_id=order_id, executor_user_id=user.id
            )
            await self.messenger.answer_callback(
                cb_id,
                notification=f"Заказ #{order.public_number} закреплён за вами! Адрес отправлен в ЛС.",
                target_chat_id=chat_id,
            )
            return

        if action == "st":
            order_id = uuid.UUID(parts[2])
            target_st = OrderStatus(parts[3])
            await self.orchestrator.transition_executor_status(
                order_id=order_id, actor_user_id=user.id, target_status=target_st
            )
            await self.messenger.answer_callback(
                cb_id,
                notification=f"Статус: {STATUS_LABELS[target_st]}",
                target_chat_id=user.max_user_id,
            )
            return

        if action == "photo":
            order_id_str = parts[2]
            stage = parts[3]
            await self.fsm.set(
                user.max_user_id,
                "exec:upload_photo",
                {"order_id": order_id_str, "stage": stage},
            )
            await self.messenger.answer_callback(cb_id, target_chat_id=user.max_user_id)
            label = "ДО начала работ" if stage == "before" else "ПОСЛЕ завершения работ"
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(text=f"📸 Отправьте 1 или несколько фото **{label}**:"),
            )
            return

        if action == "drop":
            order_id = uuid.UUID(parts[2])
            order = await self.orchestrator.unassign_executor(
                order_id=order_id,
                executor_user_id=user.id,
                reason="Отказ исполнителя в боте",
                by_foreman=False,
            )
            await self.messenger.answer_callback(
                cb_id, notification="Вы отказались от заказа", target_chat_id=user.max_user_id
            )
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=f"🚫 Вы сняты с заказа **#{order.public_number}**. Он возвращён на биржу."
                ),
            )
            return

        if action == "askadj":
            order_id = uuid.UUID(parts[2])
            async with self.session_factory() as session:
                order_opt = await session.get(Order, order_id)
                foremen = list(
                    (
                        await session.execute(
                            select(User).where(User.role == UserRole.FOREMAN.value)
                        )
                    ).scalars().all()
                )
            await self.messenger.answer_callback(
                cb_id, notification="Запрос отправлен бригадиру", target_chat_id=user.max_user_id
            )
            if order_opt:
                for fm in foremen:
                    await self.messenger.send_message(
                        MessageTarget(user_id=fm.max_user_id),
                        OutgoingMessage(
                            text=(
                                f"💰 Исполнитель **{user.display_name}** сообщил о расхождении >20% "
                                f"на заказе **#{order_opt.public_number}** (текущая цена {order_opt.final_total:.0f} ₽)."
                            ),
                            keyboard=InlineKeyboard(
                                rows=[
                                    [
                                        InlineButton(
                                            type="callback",
                                            text="✏️ Предложить новую цену",
                                            payload=f"foreman:adj:{order_opt.id}",
                                        )
                                    ]
                                ]
                            ),
                        ),
                    )
            return

        if action == "cancel":
            order_id = uuid.UUID(parts[2])
            await self.orchestrator.cancel_order(
                order_id=order_id,
                by_service=False,
                reason="Отменено клиентом через бота",
                actor_user_id=user.id,
            )
            await self.messenger.answer_callback(
                cb_id, notification="Заказ отменён", target_chat_id=user.max_user_id
            )
            return

        if action == "msg":
            order_id_str = parts[2]
            await self.fsm.set(user.max_user_id, "proxy:send_text", {"order_id": order_id_str})
            await self.messenger.answer_callback(cb_id, target_chat_id=user.max_user_id)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(text="💬 Введите сообщение для собеседника по заказу:"),
            )
            return

        if action == "rate":
            order_id = uuid.UUID(parts[2])
            stars = int(parts[3])
            await self.orchestrator.rate_order(
                order_id=order_id, client_user_id=user.id, rating=stars
            )
            await self.messenger.answer_callback(
                cb_id, notification=f"Спасибо за оценку {stars}⭐!", target_chat_id=user.max_user_id
            )
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=f"⭐ Спасибо за оценку **{stars}/5**! Будем рады помочь снова.",
                    keyboard=main_menu_keyboard(user.role),
                ),
            )
            return

        # Шаги мастера оформления заказа
        await self.messenger.answer_callback(cb_id, target_chat_id=user.max_user_id)
        rec = await self.fsm.get(user.max_user_id)
        data = rec.data if rec else {}

        if action == "zone":
            zone_code = parts[2]
            async with self.session_factory() as session:
                zone = (
                    await session.execute(select(GeoZone).where(GeoZone.code == zone_code))
                ).scalar_one_or_none()
            if zone is None:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="⚠️ Геозона не найдена."),
                )
                return
            data["geo_zone_id"] = str(zone.id)
            data["geo_zone_code"] = zone.code
            data["geo_zone_name"] = zone.name
            data["dispatch_fee"] = str(zone.dispatch_fee)
            svc = ServiceType(data.get("service_type", ServiceType.SNOW_YARD.value))
            if svc == ServiceType.CAR_RESCUE:
                await self.fsm.set(user.max_user_id, "order:rescue_address", data)
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(
                        text="Шаг 2/4: Напишите короткий ориентир/улицу (и при желании приложите фото машины в сугробе):"
                    ),
                )
            else:
                await self.fsm.set(user.max_user_id, "order:address", data)
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(
                        text=(
                            f"📍 Район: **{zone.name}**.\n"
                            "Введите точный адрес (СНТ / улица, дом, ориентир).\n"
                            "🔒 *Адрес скрыт в общем чате и передаётся только назначенному исполнителю.*"
                        )
                    ),
                )
            return

        if action == "area":
            data["area_m2"] = parts[2]
            await self.fsm.set(user.max_user_id, "order:depth", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=f"📐 Площадь: **{parts[2]} м²**. Укажите глубину снега:",
                    keyboard=depth_keyboard(),
                ),
            )
            return

        if action == "depth":
            data["depth_bucket"] = parts[2]
            await self.fsm.set(user.max_user_id, "order:access", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text="🚧 Какой подъезд на участок? (для подбора техники):",
                    keyboard=access_keyboard(),
                ),
            )
            return

        if action == "access":
            data["access_constraint"] = parts[2]
            await self.fsm.set(user.max_user_id, "order:urgency", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(text="⏱ Когда нужна уборка?", keyboard=urgency_keyboard()),
            )
            return

        if action == "urg":
            data["urgency"] = parts[2]
            await self.fsm.set(user.max_user_id, "order:snow_removal", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text="🚛 Нужен ли вывоз снега с участка?", keyboard=snow_removal_keyboard()
                ),
            )
            return

        if action == "rem":
            data["snow_removal"] = parts[2] == "yes"
            await self.fsm.set(user.max_user_id, "order:contact_mode", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text="📞 Как исполнителю связаться с вами после назначения?",
                    keyboard=contact_mode_keyboard(),
                ),
            )
            return

        if action == "veh":
            data["vehicle_type"] = parts[2]
            await self.fsm.set(user.max_user_id, "order:contact_mode", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text="Шаг 3/4: Выберите способ связи с бригадой:",
                    keyboard=contact_mode_keyboard(),
                ),
            )
            return

        if action == "contact":
            mode = parts[2]
            data["contact_mode"] = mode
            if mode == ContactMode.DIRECT_PHONE.value:
                await self.fsm.set(user.max_user_id, "order:direct_phone", data)
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(
                        text=(
                            "📞 Введите номер телефона для звонка исполнителя (например, `+7 999 123-45-67`).\n"
                            "*Отправляя номер, вы даёте согласие на его передачу назначенному исполнителю (152-ФЗ).*"
                        )
                    ),
                )
            else:
                await self._send_order_preview(user, data)
            return

        if action == "confirm":
            await self._finalize_client_order(user, data)
            return

    async def _dispatch_fsm_message(
        self,
        *,
        user: User,
        state: str,
        data: dict[str, Any],
        text: str,
        attachments: list[dict[str, Any]],
        uploaded_bytes: bytes | None,
        uploaded_filename: str | None,
        uploaded_mime: str,
    ) -> None:
        # 1. Геолокация
        if state == "order:geo":
            loc = next((a for a in attachments if a.get("type") == "location"), None)
            if loc:
                pl = loc.get("payload", {})
                lat = float(pl.get("latitude", 0))
                lon = float(pl.get("longitude", 0))
                async with self.session_factory() as session:
                    zones = list((await session.execute(select(GeoZone))).scalars().all())
                candidates = [
                    GeoZoneCandidate(
                        id=z.id,
                        code=z.code,
                        name=z.name,
                        is_active=z.is_active,
                        dispatch_fee=z.dispatch_fee,
                        base_lat=z.base_lat,
                        base_lon=z.base_lon,
                        polygon_geojson=z.polygon_geojson,
                    )
                    for z in zones
                ]
                zone, dist_km = resolve_geo_zone_by_coords(lat, lon, candidates)
                if zone is None:
                    await self.messenger.send_message(
                        MessageTarget(user_id=user.max_user_id),
                        OutgoingMessage(
                            text=(
                                "😔 Точка находится вне текущей зоны обслуживания "
                                "(Малаховка, Томилино, Красково, Быково, Лыткарино). "
                                "Выберите ближайшую зону вручную или обратитесь к диспетчеру."
                            ),
                            keyboard=geo_zone_keyboard(zones),
                        ),
                    )
                    return
                data["geo_zone_id"] = str(zone.id)
                data["geo_zone_code"] = zone.code
                data["geo_zone_name"] = zone.name
                data["dispatch_fee"] = str(zone.dispatch_fee)
                data["lat"] = lat
                data["lon"] = lon
                data["distance_km"] = str(dist_km)
                svc = ServiceType(data["service_type"])
                if svc == ServiceType.CAR_RESCUE:
                    await self.fsm.set(user.max_user_id, "order:rescue_address", data)
                    await self.messenger.send_message(
                        MessageTarget(user_id=user.max_user_id),
                        OutgoingMessage(
                            text=(
                                f"📍 Определена зона **{zone.name}** (~{dist_km:.1f} км от базы).\n"
                                "Шаг 2/4: Напишите ориентир (или сразу прикрепите фото авто в сугробе):"
                            )
                        ),
                    )
                else:
                    await self.fsm.set(user.max_user_id, "order:address", data)
                    await self.messenger.send_message(
                        MessageTarget(user_id=user.max_user_id),
                        OutgoingMessage(
                            text=f"📍 Зона **{zone.name}** определена! Укажите точный адрес (улица/СНТ, дом):"
                        ),
                    )
                return
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(text="Пожалуйста, выберите район кнопкой выше или отправьте геолокацию."),
            )
            return

        # 2. Адрес для обычных услуг
        if state == "order:address":
            if len(text) < 3:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="Укажите более подробный адрес (минимум 3 символа)."),
                )
                return
            data["address_text"] = text
            svc = ServiceType(data["service_type"])
            if svc == ServiceType.SNOW_YARD:
                await self.fsm.set(user.max_user_id, "order:area_input", data)
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(
                        text="📐 Выберите примерную площадь уборки (м²) или введите число вручную:",
                        keyboard=area_presets_keyboard(),
                    ),
                )
            elif svc == ServiceType.SNOW_ROAD:
                await self.fsm.set(user.max_user_id, "order:road_dims", data)
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(
                        text="🛣 Введите длину и ширину проезда в метрах через пробел (например: `40 4`):"
                    ),
                )
            elif svc in (ServiceType.YARD_HELP, ServiceType.MOVERS):
                await self.fsm.set(user.max_user_id, "order:hourly_params", data)
                prompt = (
                    "📦 Введите через пробел: `<число грузчиков> <часы> <этаж без лифта (0 если есть лифт)>`\n"
                    "Например: `2 3 4`"
                    if svc == ServiceType.MOVERS
                    else "🧹 Введите через пробел: `<число человек> <часы (мин. 2)>`, например: `2 3`"
                )
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id), OutgoingMessage(text=prompt)
                )
            return

        # 3. Срочно застрял — шаг 2 (ориентир / фото -> тип авто)
        if state == "order:rescue_address":
            data["address_text"] = text or f"{data.get('geo_zone_name', 'Точка по гео')}"
            if uploaded_bytes:
                # Сохраним временный файл в хранилище и привяжем после создания заказа
                key = f"tmp_rescue/{uuid.uuid4().hex}_{uploaded_filename or 'car.jpg'}"
                await self.orchestrator.storage.put_object(
                    key=key, data=uploaded_bytes, content_type=uploaded_mime
                )
                data["rescue_photo_key"] = key
                data["rescue_photo_mime"] = uploaded_mime
            await self.fsm.set(user.max_user_id, "order:rescue_vehicle", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(text="🚘 Какой тип автомобиля?", keyboard=vehicle_keyboard()),
            )
            return

        # 4. Ручной ввод площади
        if state == "order:area_input":
            try:
                area = Decimal(text.replace(",", "."))
                if area < 10 or area > 10000:
                    raise ValueError
            except (InvalidOperation, ValueError):
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="Введите число от 10 до 10 000 (площадь в м²):"),
                )
                return
            data["area_m2"] = str(area)
            await self.fsm.set(user.max_user_id, "order:depth", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=f"📐 Площадь: **{area} м²**. Укажите глубину снега:",
                    keyboard=depth_keyboard(),
                ),
            )
            return

        # 5. Длина и ширина дороги
        if state == "order:road_dims":
            nums = re.findall(r"\d+(?:[.,]\d+)?", text)
            if len(nums) < 2:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="Введите два числа через пробел, например: `50 4`"),
                )
                return
            data["length_m"] = nums[0].replace(",", ".")
            data["width_m"] = nums[1].replace(",", ".")
            data["area_m2"] = str(Decimal(data["length_m"]) * Decimal(data["width_m"]))
            await self.fsm.set(user.max_user_id, "order:depth", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=f"🛣 Проезд {data['length_m']}×{data['width_m']} м ({data['area_m2']} м²). Глубина снега:",
                    keyboard=depth_keyboard(),
                ),
            )
            return

        # 6. Почасовые параметры
        if state == "order:hourly_params":
            nums = [int(x) for x in re.findall(r"\d+", text)]
            if len(nums) < 2:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="Введите минимум 2 числа: `<люди> <часы>`, например `2 3`"),
                )
                return
            data["workers_count"] = max(1, min(10, nums[0]))
            data["hours"] = max(1, min(24, nums[1]))
            data["floors_without_lift"] = nums[2] if len(nums) >= 3 else 0
            await self.fsm.set(user.max_user_id, "order:contact_mode", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text="📞 Выберите способ связи с исполнителями:",
                    keyboard=contact_mode_keyboard(),
                ),
            )
            return

        # 7. Ввод прямого телефона с согласием 152-ФЗ
        if state == "order:direct_phone":
            digits = re.sub(r"\D", "", text)
            if len(digits) < 10:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="Введите корректный номер телефона (10–11 цифр):"),
                )
                return
            async with self.session_factory() as session:
                db_user = await session.get(User, user.id)
                if db_user:
                    db_user.shared_contact_phone = text
                    session.add(
                        UserConsent(
                            user_id=db_user.id,
                            consent_type=ConsentType.PHONE_TRANSFER.value,
                            document_version="1.0",
                        )
                    )
                    await session.commit()
            await self._send_order_preview(user, data)
            return

        # 8. Загрузка фото ДО / ПОСЛЕ исполнителем
        if state == "exec:upload_photo":
            img_att = next((a for a in attachments if a.get("type") == "image"), None)
            raw_bytes = uploaded_bytes
            if not raw_bytes and img_att and img_att.get("payload", {}).get("url"):
                raw_bytes = await self.messenger.download_media(img_att["payload"]["url"])
            if not raw_bytes:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="📸 Пожалуйста, прикрепите фотографию (изображение)."),
                )
                return
            order_id = uuid.UUID(data["order_id"])
            stage = MediaStage(data["stage"])
            await self.orchestrator.upload_order_media(
                order_id=order_id,
                uploader_user_id=user.id,
                stage=stage,
                data=raw_bytes,
                filename=uploaded_filename or f"{stage.value}.jpg",
                mime_type=uploaded_mime,
                max_photo_id=img_att["payload"].get("photo_id") if img_att else None,
                max_token=img_att["payload"].get("token") if img_att else None,
            )
            await self.fsm.clear(user.max_user_id)
            next_btn = (
                InlineButton(
                    type="callback",
                    text="▶️ Начать работу",
                    payload=f"ord:st:{order_id}:in_progress",
                )
                if stage == MediaStage.BEFORE
                else InlineButton(
                    type="callback",
                    text="✅ Завершить работу",
                    payload=f"ord:st:{order_id}:awaiting_acceptance",
                )
            )
            label = "ДО" if stage == MediaStage.BEFORE else "ПОСЛЕ"
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=f"✅ Фото «{label}» сохранено в хранилище! Можно переходить к следующему шагу:",
                    keyboard=InlineKeyboard(
                        rows=[
                            [next_btn],
                            [
                                InlineButton(
                                    type="callback",
                                    text=f"📸 Добавить ещё фото {label}",
                                    payload=f"ord:photo:{order_id}:{stage.value}",
                                )
                            ],
                        ]
                    ),
                ),
            )
            return

        # 9. Прокси-сообщение по заказу
        if state == "proxy:send_text":
            if not text:
                return
            order_id = uuid.UUID(data["order_id"])
            await self.orchestrator.send_proxy_message(
                order_id=order_id, sender_user_id=user.id, text=text
            )
            await self.fsm.clear(user.max_user_id)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(text="✅ Сообщение доставлено собеседнику через бота."),
            )
            return

        # 10. Причина спора
        if state == "dispute:reason":
            if len(text) < 3:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="Опишите замечание чуть подробнее:"),
                )
                return
            order_id = uuid.UUID(data["order_id"])
            await self.orchestrator.open_dispute(
                order_id=order_id, opener_user_id=user.id, reason=text
            )
            await self.fsm.clear(user.max_user_id)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text="⚖️ **Спор открыт.** Бригадир и диспетчер уже получили уведомление и свяжутся с вами."
                ),
            )
            return

        # 11. Регистрация исполнителя
        if state == "exec:reg_fio":
            data["fio"] = text
            await self.fsm.set(user.max_user_id, "exec:reg_inn", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text="Введите ваш ИНН самозанятого (12 цифр) или отправьте `-`, чтобы указать позже:"
                ),
            )
            return

        if state == "exec:reg_inn":
            inn = None if text == "-" else re.sub(r"\D", "", text)[:12]
            async with self.session_factory() as session:
                db_user = await session.get(User, user.id)
                if db_user:
                    parts_name = data.get("fio", "").split(" ", 1)
                    db_user.first_name = parts_name[0] or db_user.first_name
                    if len(parts_name) > 1:
                        db_user.last_name = parts_name[1]
                    if db_user.role == UserRole.CLIENT.value:
                        db_user.role = UserRole.EXECUTOR.value
                prof = await session.get(ExecutorProfile, user.id)
                if prof is None:
                    session.add(
                        ExecutorProfile(
                            user_id=user.id,
                            status=ExecutorStatus.PENDING_VERIFICATION.value,
                            is_self_employed=True,
                            inn=inn,
                        )
                    )
                session.add(
                    UserConsent(
                        user_id=user.id,
                        consent_type=ConsentType.EXECUTOR_OFFER.value,
                        document_version="1.0",
                    )
                )
                session.add(
                    UserConsent(
                        user_id=user.id,
                        consent_type=ConsentType.SAFETY_BRIEFING.value,
                        document_version="1.0",
                    )
                )
                await session.commit()
            await self.fsm.clear(user.max_user_id)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=(
                        "✅ **Заявка исполнителя отправлена на проверку!**\n"
                        "После активации администратором вы сможете брать заказы в общем чате бригады."
                    ),
                    keyboard=main_menu_keyboard(UserRole.EXECUTOR.value),
                ),
            )
            return

        # 12. Бригадир — предложение новой цены (>20%)
        if state == "foreman:adj_amount":
            try:
                amount = Decimal(text.replace(",", ".").replace(" ", ""))
                if amount <= 0:
                    raise ValueError
            except (InvalidOperation, ValueError):
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(text="Введите новую сумму числом в рублях (например: `4800`):"),
                )
                return
            data["new_total"] = str(amount)
            await self.fsm.set(user.max_user_id, "foreman:adj_reason", data)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text="Опишите причину изменения цены (например: `Фактически 180 м² и плотный наст`):"
                ),
            )
            return

        if state == "foreman:adj_reason":
            order_id = uuid.UUID(data["order_id"])
            new_total = Decimal(data["new_total"])
            await self.orchestrator.propose_price_adjustment(
                order_id=order_id,
                proposer_user_id=user.id,
                new_total=new_total,
                reason=text,
            )
            await self.fsm.clear(user.max_user_id)
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=f"✅ Предложение новой цены (**{new_total:.0f} ₽**) отправлено клиенту на согласование."
                ),
            )
            return

    def _build_calc_input(self, data: dict[str, Any]) -> PriceCalculationInput:
        svc = ServiceType(data["service_type"])
        return PriceCalculationInput(
            service_type=svc,
            dispatch_fee=to_decimal(data.get("dispatch_fee", 0)),
            area_m2=Decimal(str(data["area_m2"])) if data.get("area_m2") else None,
            length_m=Decimal(str(data["length_m"])) if data.get("length_m") else None,
            width_m=Decimal(str(data["width_m"])) if data.get("width_m") else None,
            depth_bucket=DepthBucket(data.get("depth_bucket", DepthBucket.UP_TO_10.value)),
            access_constraint=AccessConstraint(
                data.get("access_constraint", AccessConstraint.STANDARD.value)
            ),
            urgency=UrgencyLevel(data.get("urgency", UrgencyLevel.TODAY.value)),
            snow_removal=bool(data.get("snow_removal", False)),
            vehicle_type=str(data.get("vehicle_type", "sedan")),
            distance_km=Decimal(str(data.get("distance_km", 0))),
            hours=int(data.get("hours", 2)),
            workers_count=int(data.get("workers_count", 1)),
            floors_without_lift=int(data.get("floors_without_lift", 0)),
        )

    async def _send_order_preview(self, user: User, data: dict[str, Any]) -> None:
        async with self.session_factory() as session:
            tariff = await self.orchestrator.get_active_tariff(session)
        calc_input = self._build_calc_input(data)
        bd = calculate_price(calc_input, tariff.config_json)
        svc = ServiceType(data["service_type"])
        method_str = (
            f"\n🛠 Способ: {METHOD_LABELS[bd.cleaning_method]}" if bd.cleaning_method else ""
        )
        extras_str = f"\n🚛 Вывоз / доплаты: {bd.extras_cost:.0f} ₽" if bd.extras_cost > ZERO else ""
        await self.fsm.set(user.max_user_id, "order:confirm", data)
        await self.messenger.send_message(
            MessageTarget(user_id=user.max_user_id),
            OutgoingMessage(
                text=(
                    f"🧾 **Предварительный расчёт ({SERVICE_LABELS[svc]})**\n"
                    f"📍 Район: {data.get('geo_zone_name', '')}\n"
                    f"🏠 Адрес: {data.get('address_text', '')}"
                    f"{method_str}\n"
                    f"• Работы: {bd.work_cost:.0f} ₽\n"
                    f"• Выезд: {bd.dispatch_fee:.0f} ₽"
                    f"{extras_str}\n\n"
                    f"💵 **Ориентировочная цена: {bd.estimated_total:.0f} ₽**\n"
                    f"💳 Аванс к оплате сейчас (СБП): **{bd.advance_amount:.0f} ₽**\n"
                    f"📌 Остаток после фотоотчёта «ПОСЛЕ»: **{bd.remainder_amount:.0f} ₽**\n\n"
                    "*Цена ориентировочная: при расхождении объёма на месте >20% "
                    "доплата возможна только с вашего согласия.*"
                ),
                keyboard=confirm_order_keyboard(),
            ),
        )

    async def _finalize_client_order(self, user: User, data: dict[str, Any]) -> None:
        svc = ServiceType(data["service_type"])
        calc_input = self._build_calc_input(data)
        params_json: dict[str, Any] = {
            k: v
            for k, v in data.items()
            if k
            in (
                "area_m2",
                "length_m",
                "width_m",
                "depth_bucket",
                "access_constraint",
                "snow_removal",
                "vehicle_type",
                "hours",
                "workers_count",
                "floors_without_lift",
            )
        }
        if "depth_bucket" in params_json:
            params_json["depth_label"] = DEPTH_LABELS.get(params_json["depth_bucket"], "")

        order, payment = await self.orchestrator.create_order_with_advance(
            client_user_id=user.id,
            service_type=svc,
            geo_zone_id=uuid.UUID(data["geo_zone_id"]),
            address_text=str(data.get("address_text", "Адрес не указан")),
            calc_input=calc_input,
            params_json=params_json,
            lat=data.get("lat"),
            lon=data.get("lon"),
            distance_km=Decimal(str(data["distance_km"])) if data.get("distance_km") else None,
            required_executors=int(data.get("workers_count", 1)),
            contact_mode=ContactMode(data.get("contact_mode", ContactMode.BOT_PROXY.value)),
        )

        # Если клиент прикреплял фото машины при «Срочно застрял» — привязываем к заказу
        if data.get("rescue_photo_key"):
            raw = await self.orchestrator.storage.get_object(data["rescue_photo_key"])
            await self.orchestrator.upload_order_media(
                order_id=order.id,
                uploader_user_id=user.id,
                stage=MediaStage.CLIENT_PROBLEM,
                data=raw,
                filename="car_rescue.jpg",
                mime_type=data.get("rescue_photo_mime", "image/jpeg"),
            )

        await self.fsm.clear(user.max_user_id)
        timeout_min = 15 if svc == ServiceType.CAR_RESCUE else 60

        use_online_pay_btn = (
            self.settings.payment_provider == "yookassa"
            or self.settings.app_env == "development"
        )
        if use_online_pay_btn:
            msg_text = (
                f"✅ **Заказ #{order.public_number} создан!**\n"
                f"Оплатите аванс **{payment.amount:.0f} ₽** через СБП в течение {timeout_min} минут — "
                "сразу после оплаты заказ уйдёт в бригаду."
            )
            kb_rows: list[list[InlineButton]] = [
                [
                    InlineButton(
                        type="link",
                        text=f"💳 Оплатить аванс {payment.amount:.0f} ₽ (СБП)",
                        url=payment.confirmation_url or f"/mock-pay/{payment.id}",
                    )
                ],
                [
                    InlineButton(
                        type="callback",
                        text="❌ Отменить заказ",
                        payload=f"ord:cancel:{order.id}",
                    )
                ],
            ]
        else:
            msg_text = (
                f"✅ **Заказ #{order.public_number} принят!**\n"
                f"Ориентировочная стоимость: **{order.estimated_total:.0f} ₽** "
                f"(аванс **{payment.amount:.0f} ₽**).\n"
                "Диспетчер свяжется с вами в течение 10 минут для подтверждения выезда и оплаты.\n"
                f"Телефон диспетчера: {self.settings.legal_phone}"
            )
            kb_rows = [
                [
                    InlineButton(
                        type="callback",
                        text="❌ Отменить заказ",
                        payload=f"ord:cancel:{order.id}",
                    )
                ]
            ]

        await self.messenger.send_message(
            MessageTarget(user_id=user.max_user_id),
            OutgoingMessage(text=msg_text, keyboard=InlineKeyboard(rows=kb_rows)),
        )

    async def _show_client_orders(self, user: User) -> None:
        async with self.session_factory() as session:
            orders = list(
                (
                    await session.execute(
                        select(Order)
                        .where(Order.client_id == user.id)
                        .order_by(Order.created_at.desc())
                        .limit(5)
                    )
                ).scalars().all()
            )
        if not orders:
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text="📋 У вас пока нет заказов.",
                    keyboard=main_menu_keyboard(user.role),
                ),
            )
            return
        lines = ["📋 **Ваши последние заказы:**"]
        for o in orders:
            st_lbl = STATUS_LABELS.get(OrderStatus(o.status), o.status)
            svc_lbl = SERVICE_LABELS.get(ServiceType(o.service_type), o.service_type)
            lines.append(
                f"• **#{o.public_number}** — {svc_lbl} | {o.final_total:.0f} ₽ | **{st_lbl}**"
            )
        await self.messenger.send_message(
            MessageTarget(user_id=user.max_user_id),
            OutgoingMessage(
                text="\n".join(lines), keyboard=main_menu_keyboard(user.role)
            ),
        )

    async def _handle_executor_callback(
        self, user: User, cb_id: str, parts: list[str]
    ) -> None:
        action = parts[1] if len(parts) > 1 else ""
        await self.messenger.answer_callback(cb_id, target_chat_id=user.max_user_id)

        if action == "register":
            await self.fsm.set(user.max_user_id, "exec:reg_fio", {})
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=(
                        "👷 **Регистрация исполнителя «Снег-Диспетчер»**\n"
                        "Продолжая, вы принимаете оферту исполнителя и проходите инструктаж по ТБ.\n\n"
                        "Введите ваши **Имя и Фамилию**:"
                    )
                ),
            )
            return

        if action == "balance":
            async with self.session_factory() as session:
                accrued = (
                    await session.execute(
                        select(func.coalesce(func.sum(PayoutLedgerEntry.amount), 0)).where(
                            PayoutLedgerEntry.executor_id == user.id,
                            PayoutLedgerEntry.status == LedgerEntryStatus.ACCRUED.value,
                        )
                    )
                ).scalar() or ZERO
                paid = (
                    await session.execute(
                        select(func.coalesce(func.sum(PayoutLedgerEntry.amount), 0)).where(
                            PayoutLedgerEntry.executor_id == user.id,
                            PayoutLedgerEntry.status == LedgerEntryStatus.PAID.value,
                        )
                    )
                ).scalar() or ZERO
                prof = await session.get(ExecutorProfile, user.id)
            rating_str = f"⭐ Рейтинг: **{prof.rating}** ({prof.completed_orders_count} заказов)" if prof else ""
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(
                    text=(
                        f"💰 **Баланс исполнителя ({user.display_name})**\n"
                        f"• К выплате (начислено): **{Decimal(accrued):.0f} ₽**\n"
                        f"• Выплачено за всё время: **{Decimal(paid):.0f} ₽**\n"
                        f"{rating_str}"
                    ),
                    keyboard=main_menu_keyboard(user.role),
                ),
            )
            return

        if action == "my_jobs":
            async with self.session_factory() as session:
                asgns = list(
                    (
                        await session.execute(
                            select(OrderAssignment).where(
                                OrderAssignment.executor_id == user.id,
                                OrderAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                            )
                        )
                    ).scalars().all()
                )
                orders = [await session.get(Order, a.order_id) for a in asgns]
            active_orders = [o for o in orders if o is not None]
            if not active_orders:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(
                        text="📦 У вас нет активных заказов. Следите за карточками в общем чате бригады!",
                        keyboard=main_menu_keyboard(user.role),
                    ),
                )
                return
            for o in active_orders:
                await self.messenger.send_message(
                    MessageTarget(user_id=user.max_user_id),
                    OutgoingMessage(
                        text=(
                            f"📦 **Заказ #{o.public_number}** — {STATUS_LABELS[OrderStatus(o.status)]}\n"
                            f"🏠 Адрес: {o.address_text}\n💵 Итого: {o.final_total:.0f} ₽"
                        ),
                        keyboard=self.orchestrator._executor_order_keyboard(o),
                    ),
                )

    async def _handle_foreman_callback(
        self, user: User, cb_id: str, parts: list[str]
    ) -> None:
        if user.role not in (UserRole.FOREMAN.value, UserRole.ADMIN.value, UserRole.OWNER.value):
            await self.messenger.answer_callback(
                cb_id, notification="Доступно только бригадиру", target_chat_id=user.max_user_id
            )
            return
        action = parts[1] if len(parts) > 1 else ""
        await self.messenger.answer_callback(cb_id, target_chat_id=user.max_user_id)

        if action == "panel":
            async with self.session_factory() as session:
                hot_orders = list(
                    (
                        await session.execute(
                            select(Order)
                            .where(
                                Order.status.in_(
                                    [
                                        OrderStatus.SEARCHING_EXECUTOR.value,
                                        OrderStatus.ARRIVED.value,
                                        OrderStatus.DISPUTED.value,
                                    ]
                                )
                            )
                            .order_by(Order.created_at.desc())
                            .limit(8)
                        )
                    ).scalars().all()
                )
                open_disputes = (
                    await session.execute(
                        select(func.count(Dispute.id)).where(
                            Dispute.status.in_([DisputeStatus.OPEN.value, DisputeStatus.IN_REVIEW.value])
                        )
                    )
                ).scalar() or 0
            lines = [f"🛠 **Панель бригадира** (открытых споров: {open_disputes})"]
            kb_rows: list[list[InlineButton]] = []
            for o in hot_orders:
                lines.append(
                    f"• **#{o.public_number}** | {o.public_area_hint} | {STATUS_LABELS[OrderStatus(o.status)]} | {o.final_total:.0f} ₽"
                )
                if o.status == OrderStatus.ARRIVED.value:
                    kb_rows.append(
                        [
                            InlineButton(
                                type="callback",
                                text=f"✏️ Скорректировать цену #{o.public_number}",
                                payload=f"foreman:adj:{o.id}",
                            )
                        ]
                    )
            if not hot_orders:
                lines.append("Сейчас нет заказов, требующих вмешательства бригадира.")
            kb_rows.append(
                [InlineButton(type="callback", text="🏠 Главное меню", payload="menu:home")]
            )
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(text="\n".join(lines), keyboard=InlineKeyboard(rows=kb_rows)),
            )
            return

        if action == "adj":
            order_id = parts[2]
            await self.fsm.set(user.max_user_id, "foreman:adj_amount", {"order_id": order_id})
            await self.messenger.send_message(
                MessageTarget(user_id=user.max_user_id),
                OutgoingMessage(text="💰 Введите новую итоговую стоимость заказа (в рублях):"),
            )
