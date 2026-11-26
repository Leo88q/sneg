"""Центральный прикладной сервис управления заказами (ТЗ B5–B8, B11)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import Settings
from src.core.enums import (
    METHOD_LABELS,
    SERVICE_LABELS,
    STATUS_LABELS,
    AdminRole,
    AssignmentRole,
    AssignmentStatus,
    ChatPurpose,
    CleaningMethod,
    ContactMode,
    DisputeStatus,
    ExecutorStatus,
    LedgerEntryStatus,
    LedgerEntryType,
    MediaStage,
    OrderPaymentStatus,
    OrderStatus,
    PaymentPurpose,
    PaymentStatus,
    PayoutBatchStatus,
    PriceAdjustmentStatus,
    RefundStatus,
    ServiceType,
    SubscriptionStatus,
    UserRole,
)
from src.core.exceptions import (
    InvalidStateTransitionError,
    MediaRequiredError,
    NotFoundError,
    OrderAlreadyTakenError,
    PermissionDeniedError,
    ValidationAppError,
)
from src.domain.geo.service import build_public_area_hint, normalize_cluster_key
from src.domain.orders.state_machine import assert_order_transition
from src.domain.pricing.calculator import (
    ZERO,
    PriceCalculationInput,
    calculate_cancellation_refund,
    calculate_executor_earnings,
    calculate_price,
    resolve_group_discount_pct,
    round_to_step,
    to_decimal,
)
from src.infra.db.models import (
    AdminAccount,
    BotChat,
    Dispute,
    ExecutorProfile,
    GeoZone,
    Order,
    OrderAssignment,
    OrderGroup,
    OrderMedia,
    OrderMessage,
    OrderStatusHistory,
    Payment,
    PayoutBatch,
    PayoutLedgerEntry,
    PriceAdjustment,
    Refund,
    Subscription,
    TariffConfig,
    User,
    utcnow,
)
from src.infra.storage.base import FileStorage
from src.services.messenger.base import (
    InlineButton,
    InlineKeyboard,
    MessageTarget,
    MessengerTransport,
    OutgoingMessage,
)
from src.services.payments.base import (
    CreatePaymentCommand,
    CreateRefundCommand,
    PaymentProvider,
    ReceiptItem,
)

logger = logging.getLogger(__name__)

ACTIVE_ASSIGNMENT_STATUSES = (
    AssignmentStatus.ASSIGNED.value,
    AssignmentStatus.EN_ROUTE.value,
    AssignmentStatus.ARRIVED.value,
    AssignmentStatus.IN_PROGRESS.value,
)


class OrderOrchestrator:
    """Единая точка выполнения всех операций с заказами (бот, вебхуки, админка, симулятор)."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        messenger: MessengerTransport,
        payment_provider: PaymentProvider,
        storage: FileStorage,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.messenger = messenger
        self.payment_provider = payment_provider
        self.storage = storage
        # Локальные блокировки гарантируют атомарность «Беру» на SQLite и дополняют FOR UPDATE на PG
        self._order_locks: dict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pub_num_lock = asyncio.Lock()

    async def _notify_max_user(self, max_user_id: int | None, msg: OutgoingMessage) -> None:
        """Отправляет ЛС пользователю MAX; пропускает веб-клиентов (max_user_id <= 0)."""
        if max_user_id is None or max_user_id <= 0:
            return
        await self.messenger.send_message(MessageTarget(user_id=max_user_id), msg)

    async def _allocate_public_number(self, session: AsyncSession) -> int:
        async with self._pub_num_lock:
            max_num = (await session.execute(select(func.max(Order.public_number)))).scalar()
            return int(max_num) + 1 if max_num is not None else 1001

    async def get_active_tariff(self, session: AsyncSession) -> TariffConfig:
        tariff = (
            await session.execute(select(TariffConfig).where(TariffConfig.is_active.is_(True)))
        ).scalar_one_or_none()
        if tariff is None:
            raise NotFoundError("Активная конфигурация тарифа не найдена")
        return tariff

    async def get_dispatch_chat_id(self, session: AsyncSession) -> int | None:
        if self.settings.max_dispatch_chat_id:
            return self.settings.max_dispatch_chat_id
        chat = (
            await session.execute(
                select(BotChat)
                .where(
                    BotChat.chat_purpose == ChatPurpose.DISPATCH_POOL.value,
                    BotChat.is_active.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return chat.chat_id if chat else None

    async def get_alerts_chat_ids(self, session: AsyncSession) -> list[int]:
        ids: list[int] = []
        if self.settings.max_alerts_chat_id:
            ids.append(self.settings.max_alerts_chat_id)
        chats = (
            await session.execute(
                select(BotChat).where(
                    BotChat.chat_purpose == ChatPurpose.FOREMAN_ALERTS.value,
                    BotChat.is_active.is_(True),
                )
            )
        ).scalars().all()
        for ch in chats:
            if ch.chat_id not in ids:
                ids.append(ch.chat_id)
        return ids

    async def _notify_alerts_new_order(self, order_id: uuid.UUID) -> None:
        """Отправляет владельцу/диспетчеру карточку новой заявки в привязанный чат MAX."""
        async with self.session_factory() as session:
            alert_chats = await self.get_alerts_chat_ids(session)
            if not alert_chats:
                return
            order = await session.get(Order, order_id)
            if order is None:
                return
            client = await session.get(User, order.client_id)
            zone = await session.get(GeoZone, order.geo_zone_id) if order.geo_zone_id else None

            svc_label = SERVICE_LABELS.get(ServiceType(order.service_type), order.service_type)
            method_str = ""
            if order.cleaning_method:
                try:
                    method_str = METHOD_LABELS[CleaningMethod(order.cleaning_method)]
                except ValueError:
                    method_str = order.cleaning_method

            source_label = "Сайт chistodvor.ru" if (client and client.max_user_id <= 0) else "MAX-бот"
            client_name = client.display_name if client else "Клиент"
            phone_line = (
                f"📞 Телефон: {client.shared_contact_phone}\n"
                if client and client.shared_contact_phone
                else ""
            )
            zone_name = zone.name if zone else "—"
            comment_line = f"💬 Комментарий: {order.client_comment}\n" if order.client_comment else ""

            ue = (order.pricing_snapshot or {}).get("unit_economics") or {}
            ue_lines = ""
            if ue:
                rental = to_decimal(ue.get("equipment_rental_rub", 0))
                profit = to_decimal(ue.get("owner_net_profit_rub", 0))
                margin = ue.get("owner_margin_pct", 0)
                if rental > ZERO:
                    ue_lines += f"🚜 Бюджет аренды техники: **{rental:.0f} ₽**\n"
                ue_lines += f"📈 Прогноз чистой прибыли ИП: **+{profit:.0f} ₽ ({margin}%)**\n"

            base_url = (self.settings.public_base_url or "https://chistodvor.ru").rstrip("/")
            text = (
                f"🔔 **НОВАЯ ЗАЯВКА #{order.public_number}** ({source_label})\n"
                f"• Услуга: **{svc_label}**"
                + (f" ({method_str})" if method_str else "")
                + "\n"
                f"• Зона: **{zone_name}**\n"
                f"• Адрес: **{order.address_text}**\n"
                f"• Клиент: {client_name}\n"
                f"{phone_line}"
                f"{comment_line}"
                f"• Сумма: **{order.final_total:.0f} ₽** (аванс **{order.advance_amount:.0f} ₽**)\n"
                f"{ue_lines}\n"
                f"🔗 Админка: {base_url}/admin\n"
                "Нажмите кнопку ниже, чтобы сразу отправить карточку рабочим в чат бригады:"
            )
            kb = InlineKeyboard(
                rows=[
                    [
                        InlineButton(
                            type="callback",
                            text="🚀 Отправить в чат рабочих",
                            payload=f"disp:publish:{order.id}",
                        )
                    ],
                    [
                        InlineButton(
                            type="callback",
                            text="❌ Отменить заявку",
                            payload=f"disp:cancel:{order.id}",
                        )
                    ],
                ]
            )
            msg = OutgoingMessage(text=text, keyboard=kb)

        for chat_id in alert_chats:
            try:
                await self.messenger.send_message(MessageTarget(chat_id=chat_id), msg)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Не удалось отправить уведомление о заявке #%s в чат %s: %s",
                    order_id,
                    chat_id,
                    exc,
                )

    async def publish_order_to_dispatch(
        self,
        *,
        order_id: uuid.UUID,
        actor_label: str,
        actor_user_id: uuid.UUID | None = None,
        actor_admin_id: uuid.UUID | None = None,
        mark_advance_paid: bool = False,
    ) -> Order:
        """Переводит заявку в поиск исполнителя и публикует карточку в чат рабочих (бригады)."""
        client_max_id: int | None = None
        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")

            dispatch_chat_id = order.dispatch_chat_id or await self.get_dispatch_chat_id(session)
            if dispatch_chat_id is None:
                raise ValidationAppError(
                    "Чат рабочих (биржа) ещё не привязан: добавьте бота в группу бригады "
                    "и напишите там /bind_dispatch_chat"
                )

            if order.status not in (
                OrderStatus.AWAITING_ADVANCE.value,
                OrderStatus.SEARCHING_EXECUTOR.value,
                OrderStatus.PARTIALLY_ASSIGNED.value,
            ):
                raise InvalidStateTransitionError(
                    f"Нельзя отправить в чат рабочих заказ в статусе «{STATUS_LABELS.get(OrderStatus(order.status), order.status)}»"
                )

            prev_status = order.status
            if order.status == OrderStatus.AWAITING_ADVANCE.value:
                order.status = OrderStatus.SEARCHING_EXECUTOR.value
                order.dispatch_wave = 1
                if mark_advance_paid and order.paid_amount < order.advance_amount:
                    order.paid_amount = order.advance_amount
                    order.payment_status = OrderPaymentStatus.ADVANCE_PAID.value
                await self._log_status(
                    session,
                    order=order,
                    from_status=prev_status,
                    to_status=order.status,
                    actor_user_id=actor_user_id,
                    actor_admin_id=actor_admin_id,
                    reason=f"Отправлено в чат рабочих ({actor_label})",
                )

            client = await session.get(User, order.client_id)
            if client and client.max_user_id > 0 and prev_status == OrderStatus.AWAITING_ADVANCE.value:
                client_max_id = client.max_user_id

            await session.commit()
            await session.refresh(order)

        await self._publish_or_update_dispatch_card(order_id, show_take_button=True)

        if client_max_id is not None:
            await self._notify_max_user(
                client_max_id,
                OutgoingMessage(
                    text=(
                        f"✅ **Заказ #{order.public_number} подтверждён диспетчером и передан бригаде!**\n"
                        "Как только исполнитель возьмёт заявку, мы сразу сообщим вам."
                    )
                ),
            )

        return order

    async def _log_status(
        self,
        session: AsyncSession,
        *,
        order: Order,
        from_status: str | None,
        to_status: str,
        actor_user_id: uuid.UUID | None = None,
        actor_admin_id: uuid.UUID | None = None,
        reason: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            OrderStatusHistory(
                order_id=order.id,
                from_status=from_status,
                to_status=to_status,
                actor_user_id=actor_user_id,
                actor_admin_id=actor_admin_id,
                reason=reason,
                meta_json=meta or {},
            )
        )

    def _build_dispatch_card_text(
        self,
        order: Order,
        *,
        taken_names: list[str] | None = None,
        canceled_label: str | None = None,
    ) -> str:
        svc_label = SERVICE_LABELS.get(ServiceType(order.service_type), order.service_type)
        prefix = "🚨 СРОЧНО | " if order.service_type == ServiceType.CAR_RESCUE.value else "❄️ "
        params = order.params_json or {}
        spec_parts: list[str] = []
        if params.get("area_m2"):
            spec_parts.append(f"~{params['area_m2']} м²")
        if params.get("length_m") and params.get("width_m"):
            spec_parts.append(f"{params['length_m']}×{params['width_m']} м")
        if params.get("depth_label"):
            spec_parts.append(f"глубина {params['depth_label']}")
        if order.cleaning_method:
            spec_parts.append(
                METHOD_LABELS.get(CleaningMethod(order.cleaning_method), order.cleaning_method)
            )
        if params.get("snow_removal"):
            spec_parts.append("с вывозом снега")
        if params.get("hours"):
            spec_parts.append(f"{params['hours']} ч")
        if params.get("vehicle_type"):
            spec_parts.append(f"авто: {params['vehicle_type']}")

        snap = order.pricing_snapshot or {}
        share_pct = to_decimal(snap.get("executor_share_pct", 65))
        disp_fee = to_decimal(snap.get("dispatch_fee", 0))
        disp_comp_pct = to_decimal(snap.get("dispatch_compensation_pct", 100))
        earn, comp, bonus = calculate_executor_earnings(
            final_total=order.final_total,
            dispatch_fee=disp_fee,
            executor_share_pct=share_pct,
            dispatch_comp_pct=disp_comp_pct,
            bonus_amount=order.executor_bonus_amount,
            workers_count=order.required_executors,
        )
        est_worker_pay = earn + comp + bonus

        taken = taken_names or []
        free_slots = max(0, order.required_executors - len(taken))
        lines = [
            f"{prefix}**ЗАКАЗ #{order.public_number}** · {svc_label}",
            f"📍 Район: {order.public_area_hint}",
        ]
        if spec_parts:
            lines.append(f"📐 Параметры: {', '.join(spec_parts)}")
        lines.append(f"💰 Ваше вознаграждение: ~{est_worker_pay:.0f} ₽ (доля {share_pct:.0f}% + выезд)")
        if order.executor_bonus_amount > ZERO:
            lines.append(f"🔥 Доп. бонус за срочность: +{order.executor_bonus_amount:.0f} ₽")
        lines.append(
            f"👥 Требуется: {order.required_executors} | Свободно слотов: {free_slots}"
        )
        if taken:
            lines.append(f"✅ Забрал(и): {', '.join(taken)}")
        if canceled_label:
            lines.append(f"❌ {canceled_label}")
        return "\n".join(lines)

    async def _publish_or_update_dispatch_card(
        self,
        order_id: uuid.UUID,
        *,
        taken_names: list[str] | None = None,
        show_take_button: bool = True,
        canceled_label: str | None = None,
    ) -> None:
        """Публикует или обновляет карточку в общем чате СТРОГО вне открытой транзакции."""
        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                return
            chat_id = order.dispatch_chat_id or await self.get_dispatch_chat_id(session)
            if chat_id is None:
                logger.warning(
                    "No active dispatch chat configured for order #%s", order.public_number
                )
                return
            existing_msg_id = order.dispatch_message_id
            text = self._build_dispatch_card_text(
                order, taken_names=taken_names, canceled_label=canceled_label
            )

        kb = (
            InlineKeyboard(
                rows=[
                    [
                        InlineButton(
                            type="callback",
                            text="✋ Беру заказ",
                            payload=f"ord:take:{order_id}",
                        )
                    ]
                ]
            )
            if show_take_button
            else None
        )
        msg = OutgoingMessage(text=text, keyboard=kb)

        if existing_msg_id:
            try:
                await self.messenger.edit_message(
                    existing_msg_id, msg, clear_keyboard=not show_take_button
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Не удалось отредактировать карточку заказа #%s (%s): %s",
                    order_id,
                    existing_msg_id,
                    exc,
                )
        else:
            sent = await self.messenger.send_message(MessageTarget(chat_id=chat_id), msg)
            async with self.session_factory() as session:
                order_upd = await session.get(Order, order_id)
                if order_upd is not None:
                    order_upd.dispatch_chat_id = chat_id
                    order_upd.dispatch_message_id = sent.message_id
                    await session.commit()

    async def create_order_with_advance(
        self,
        *,
        client_user_id: uuid.UUID,
        service_type: ServiceType,
        geo_zone_id: uuid.UUID,
        address_text: str,
        calc_input: PriceCalculationInput,
        params_json: dict[str, Any],
        lat: float | None = None,
        lon: float | None = None,
        distance_km: Decimal | None = None,
        required_executors: int = 1,
        contact_mode: ContactMode = ContactMode.BOT_PROXY,
        client_comment: str | None = None,
        scheduled_for: datetime | None = None,
        apply_group_discount: bool = True,
    ) -> tuple[Order, Payment]:
        async with self.session_factory() as session:
            client = await session.get(User, client_user_id)
            if client is None:
                raise NotFoundError("Клиент не найден")
            zone = await session.get(GeoZone, geo_zone_id)
            if zone is None or not zone.is_active:
                raise ValidationAppError("Выбранная геозона недоступна")
            tariff = await self.get_active_tariff(session)

            # Проверка соседского кластера (Этап 3 — групповая скидка)
            service_day = (scheduled_for or utcnow()).date()
            cluster_key = normalize_cluster_key(zone.code, address_text)
            order_group: OrderGroup | None = None
            group_discount_pct = ZERO

            if apply_group_discount and service_type in (
                ServiceType.SNOW_YARD,
                ServiceType.SNOW_ROAD,
            ):
                order_group = (
                    await session.execute(
                        select(OrderGroup).where(
                            OrderGroup.service_date == service_day,
                            OrderGroup.geo_zone_id == zone.id,
                            OrderGroup.cluster_key == cluster_key,
                        )
                    )
                ).scalar_one_or_none()
                projected_count = (order_group.orders_count + 1) if order_group else 1
                group_discount_pct = resolve_group_discount_pct(
                    projected_count, tariff.config_json
                )

            calc_input.dispatch_fee = zone.dispatch_fee
            calc_input.group_discount_pct = group_discount_pct
            breakdown = calculate_price(calc_input, tariff.config_json)

            pub_num = await self._allocate_public_number(session)
            public_hint = build_public_area_hint(zone.name, address_text)

            order = Order(
                public_number=pub_num,
                client_id=client.id,
                service_type=service_type.value,
                status=OrderStatus.AWAITING_ADVANCE.value,
                payment_status=OrderPaymentStatus.ADVANCE_PENDING.value,
                geo_zone_id=zone.id,
                address_text=address_text,
                public_area_hint=public_hint,
                cluster_key=cluster_key,
                lat=lat,
                lon=lon,
                distance_from_base_km=distance_km,
                params_json=params_json,
                cleaning_method=(
                    breakdown.cleaning_method.value if breakdown.cleaning_method else None
                ),
                urgency=calc_input.urgency.value,
                required_executors=max(1, required_executors),
                scheduled_for=scheduled_for or utcnow(),
                tariff_config_id=tariff.id,
                pricing_snapshot=breakdown.pricing_snapshot,
                estimated_total=breakdown.estimated_total,
                final_total=breakdown.estimated_total,
                advance_amount=breakdown.advance_amount,
                paid_amount=ZERO,
                group_discount_amount=breakdown.group_discount_amount,
                contact_mode=contact_mode.value,
                client_comment=client_comment,
            )
            session.add(order)
            await session.flush()

            await self._log_status(
                session,
                order=order,
                from_status=OrderStatus.DRAFT.value,
                to_status=OrderStatus.AWAITING_ADVANCE.value,
                actor_user_id=client.id,
                reason="Оформлен заказ, создан платёж аванса",
            )

            # Создаём платёж аванса (СБП capture=True + чек 54-ФЗ partial_prepayment)
            pay_id = uuid.uuid4()
            idem_key = uuid.uuid4()
            timeout_min = 15 if service_type == ServiceType.CAR_RESCUE else 60
            return_url = (
                f"{self.settings.public_base_url.rstrip('/')}/pay/return?order={order.public_number}"
                if self.settings.public_base_url
                else f"/pay/return?order={order.public_number}"
            )
            svc_label = SERVICE_LABELS.get(service_type, service_type.value)
            pay_res = await self.payment_provider.create_payment(
                CreatePaymentCommand(
                    internal_payment_id=pay_id,
                    idempotency_key=idem_key,
                    amount=breakdown.advance_amount,
                    description=f"Аванс по заказу #{order.public_number}: {svc_label}",
                    return_url=return_url,
                    metadata={"order_id": str(order.id), "purpose": PaymentPurpose.ADVANCE.value},
                    customer_email=client.email,
                    customer_phone=client.shared_contact_phone or client.bot_phone,
                    receipt_items=[
                        ReceiptItem(
                            description=f"Аванс: {svc_label} (заказ #{order.public_number})",
                            amount=breakdown.advance_amount,
                            payment_mode="partial_prepayment",
                        )
                    ],
                )
            )
            payment = Payment(
                id=pay_id,
                order_id=order.id,
                purpose=PaymentPurpose.ADVANCE.value,
                provider=pay_res.provider,
                provider_payment_id=pay_res.provider_payment_id,
                idempotency_key=idem_key,
                amount=breakdown.advance_amount,
                currency="RUB",
                status=PaymentStatus.PENDING.value,
                confirmation_url=pay_res.confirmation_url,
                receipt_status="pending",
                expires_at=utcnow() + timedelta(minutes=timeout_min),
                raw_response=pay_res.raw_response,
            )
            session.add(payment)
            await session.commit()
            await session.refresh(order)
            await session.refresh(payment)

        await self._notify_alerts_new_order(order.id)
        return order, payment

    async def _update_order_group_after_advance(
        self, session: AsyncSession, order: Order
    ) -> list[tuple[User, Order, Decimal]]:
        """Обновляет кластер соседских заказов и пересчитывает скидку соседям (Этап 3)."""
        if order.service_type not in (ServiceType.SNOW_YARD.value, ServiceType.SNOW_ROAD.value):
            return []
        if not order.geo_zone_id or not order.cluster_key:
            return []

        service_day = (order.scheduled_for or order.created_at).date()
        group = (
            await session.execute(
                select(OrderGroup).where(
                    OrderGroup.service_date == service_day,
                    OrderGroup.geo_zone_id == order.geo_zone_id,
                    OrderGroup.cluster_key == order.cluster_key,
                )
            )
        ).scalar_one_or_none()
        if group is None:
            group = OrderGroup(
                service_date=service_day,
                geo_zone_id=order.geo_zone_id,
                cluster_key=order.cluster_key,
                orders_count=0,
                discount_pct=ZERO,
            )
            session.add(group)
            await session.flush()

        order.order_group_id = group.id
        group.orders_count += 1
        tariff = await self.get_active_tariff(session)
        new_disc_pct = resolve_group_discount_pct(group.orders_count, tariff.config_json)
        notifications: list[tuple[User, Order, Decimal]] = []

        if new_disc_pct > group.discount_pct:
            group.discount_pct = new_disc_pct
            # Пересчитываем остаток всем ранее оплатившим соседям в этой группе
            peers = (
                await session.execute(
                    select(Order).where(
                        Order.order_group_id == group.id,
                        Order.id != order.id,
                        Order.status.notin_(
                            [
                                OrderStatus.COMPLETED.value,
                                OrderStatus.CANCELED_BY_CLIENT.value,
                                OrderStatus.CANCELED_BY_SERVICE.value,
                                OrderStatus.CANCELED_TIMEOUT.value,
                            ]
                        ),
                    )
                )
            ).scalars().all()
            step = int(tariff.config_json.get("rounding_step_rub", 50))
            min_total = to_decimal(tariff.config_json.get("min_order_total_rub", 2000))
            for peer in peers:
                snap = peer.pricing_snapshot or {}
                subtotal = (
                    to_decimal(snap.get("work_cost", peer.estimated_total))
                    + to_decimal(snap.get("dispatch_fee", 0))
                    + to_decimal(snap.get("extras_cost", 0))
                )
                disc_rub = (subtotal * new_disc_pct / Decimal("100")).quantize(Decimal("0.01"))
                new_total = round_to_step(max(min_total, subtotal - disc_rub), step)
                if new_total < peer.final_total:
                    saved = (peer.final_total - new_total).quantize(Decimal("0.01"))
                    peer.final_total = new_total
                    peer.group_discount_amount = disc_rub
                    peer_client = await session.get(User, peer.client_id)
                    if peer_client:
                        notifications.append((peer_client, peer, saved))

        return notifications

    async def confirm_payment_succeeded(
        self, payment_id: uuid.UUID
    ) -> tuple[Payment, Order | None]:
        """Идемпотентная обработка подтверждённого платежа (аванс или финальная доплата)."""
        neighbor_alerts: list[tuple[User, Order, Decimal]] = []
        client_to_notify: User | None = None
        executors_to_notify: list[tuple[User, Decimal]] = []
        transitioned_to: OrderStatus | None = None

        async with self.session_factory() as session:
            payment = await session.get(Payment, payment_id)
            if payment is None:
                raise NotFoundError("Платёж не найден")

            order = await session.get(Order, payment.order_id) if payment.order_id else None

            if payment.status == PaymentStatus.SUCCEEDED.value:
                return payment, order

            payment.status = PaymentStatus.SUCCEEDED.value
            payment.paid_at = utcnow()
            payment.receipt_status = "issued"

            if order is None:
                await session.commit()
                return payment, None

            order.paid_amount = (order.paid_amount + payment.amount).quantize(Decimal("0.01"))
            client_to_notify = await session.get(User, order.client_id)

            if (
                payment.purpose in (PaymentPurpose.ADVANCE.value, PaymentPurpose.FULL_PREPAYMENT.value)
                and order.status == OrderStatus.AWAITING_ADVANCE.value
            ):
                assert_order_transition(OrderStatus(order.status), OrderStatus.SEARCHING_EXECUTOR)
                old_st = order.status
                order.status = OrderStatus.SEARCHING_EXECUTOR.value
                order.payment_status = (
                    OrderPaymentStatus.FULLY_PAID.value
                    if order.paid_amount >= order.final_total
                    else OrderPaymentStatus.ADVANCE_PAID.value
                )
                order.dispatch_wave = 1
                await self._log_status(
                    session,
                    order=order,
                    from_status=old_st,
                    to_status=order.status,
                    reason=f"Подтверждён аванс {payment.amount:.0f} ₽",
                    meta={"payment_id": str(payment.id)},
                )
                neighbor_alerts = await self._update_order_group_after_advance(session, order)
                transitioned_to = OrderStatus.SEARCHING_EXECUTOR

            elif (
                payment.purpose == PaymentPurpose.FINAL.value
                and order.status == OrderStatus.AWAITING_FINAL_PAYMENT.value
            ):
                assert_order_transition(OrderStatus(order.status), OrderStatus.COMPLETED)
                old_st = order.status
                order.status = OrderStatus.COMPLETED.value
                order.payment_status = OrderPaymentStatus.FULLY_PAID.value
                order.completed_at = utcnow()
                await self._log_status(
                    session,
                    order=order,
                    from_status=old_st,
                    to_status=order.status,
                    reason=f"Оплачен остаток {payment.amount:.0f} ₽, заказ закрыт",
                    meta={"payment_id": str(payment.id)},
                )
                executors_to_notify = await self._accrue_order_earnings(session, order)
                transitioned_to = OrderStatus.COMPLETED

            await session.commit()
            await session.refresh(payment)
            await session.refresh(order)

        # Уведомления после коммита транзакции
        if transitioned_to == OrderStatus.SEARCHING_EXECUTOR and order:
            await self._publish_or_update_dispatch_card(order.id, show_take_button=True)

        if client_to_notify and order:
            if transitioned_to == OrderStatus.SEARCHING_EXECUTOR:
                await self._notify_max_user(client_to_notify.max_user_id, OutgoingMessage(
                        text=(
                            f"✅ **Аванс {payment.amount:.0f} ₽ принят!**\n"
                            f"Заказ **#{order.public_number}** передан бригаде. "
                            "Обычно исполнитель назначается за 5–10 минут."
                        ),
                        keyboard=InlineKeyboard(
                            rows=[
                                [
                                    InlineButton(
                                        type="callback",
                                        text="❌ Отменить заказ",
                                        payload=f"ord:cancel:{order.id}",
                                    )
                                ]
                            ]
                        ),
                    ),
                )
            elif transitioned_to == OrderStatus.COMPLETED:
                await self._notify_max_user(client_to_notify.max_user_id, OutgoingMessage(
                        text=(
                            f"🎉 **Заказ #{order.public_number} полностью оплачен и завершён!**\n"
                            "Спасибо, что выбрали «Снег-Диспетчер»! Оцените, пожалуйста, работу бригады:"
                        ),
                        keyboard=InlineKeyboard(
                            rows=[
                                [
                                    InlineButton(
                                        type="callback",
                                        text=f"⭐ {i}",
                                        payload=f"ord:rate:{order.id}:{i}",
                                    )
                                    for i in range(1, 6)
                                ]
                            ]
                        ),
                    ),
                )

        for exec_user, total_earned in executors_to_notify:
            await self._notify_max_user(exec_user.max_user_id, OutgoingMessage(
                    text=(
                        f"💰 **Заказ #{order.public_number} закрыт!**\n"
                        f"Вам начислено: **+{total_earned:.0f} ₽** (отображается в разделе «Мой баланс»)."
                    )
                ),
            )

        for peer_client, peer_order, saved in neighbor_alerts:
            await self._notify_max_user(peer_client.max_user_id, OutgoingMessage(
                    text=(
                        f"🏘 **Соседская скидка активирована!**\n"
                        f"На вашей улице/в СНТ появился ещё один заказ на эту дату. "
                        f"Цена заказа **#{peer_order.public_number}** снижена на **{saved:.0f} ₽** "
                        f"(новый итог: {peer_order.final_total:.0f} ₽)."
                    )
                ),
            )

        return payment, order

    async def cancel_payment(self, payment_id: uuid.UUID) -> Payment:
        async with self.session_factory() as session:
            payment = await session.get(Payment, payment_id)
            if payment is None:
                raise NotFoundError("Платёж не найден")
            if payment.status == PaymentStatus.PENDING.value:
                payment.status = PaymentStatus.CANCELED.value
                await session.commit()
                await session.refresh(payment)
            return payment

    async def _accrue_order_earnings(
        self, session: AsyncSession, order: Order
    ) -> list[tuple[User, Decimal]]:
        assignments = (
            await session.execute(
                select(OrderAssignment).where(
                    OrderAssignment.order_id == order.id,
                    OrderAssignment.status.in_(
                        [
                            AssignmentStatus.ASSIGNED.value,
                            AssignmentStatus.EN_ROUTE.value,
                            AssignmentStatus.ARRIVED.value,
                            AssignmentStatus.IN_PROGRESS.value,
                            AssignmentStatus.COMPLETED.value,
                        ]
                    ),
                )
            )
        ).scalars().all()
        if not assignments:
            return []

        snap = order.pricing_snapshot or {}
        disp_fee = to_decimal(snap.get("dispatch_fee", 0))
        disp_comp_pct = to_decimal(snap.get("dispatch_compensation_pct", 100))
        notified: list[tuple[User, Decimal]] = []

        for asgn in assignments:
            asgn.status = AssignmentStatus.COMPLETED.value
            asgn.finished_at = asgn.finished_at or utcnow()
            earn, comp, bonus = calculate_executor_earnings(
                final_total=order.final_total,
                dispatch_fee=disp_fee,
                executor_share_pct=asgn.executor_share_pct,
                dispatch_comp_pct=disp_comp_pct,
                bonus_amount=order.executor_bonus_amount,
                workers_count=len(assignments),
            )
            if earn > ZERO:
                session.add(
                    PayoutLedgerEntry(
                        executor_id=asgn.executor_id,
                        order_id=order.id,
                        entry_type=LedgerEntryType.ORDER_EARNING.value,
                        amount=earn,
                        status=LedgerEntryStatus.ACCRUED.value,
                        comment=f"Заказ #{order.public_number} ({asgn.executor_share_pct:.0f}%)",
                    )
                )
            if comp > ZERO:
                session.add(
                    PayoutLedgerEntry(
                        executor_id=asgn.executor_id,
                        order_id=order.id,
                        entry_type=LedgerEntryType.DISPATCH_COMPENSATION.value,
                        amount=comp,
                        status=LedgerEntryStatus.ACCRUED.value,
                        comment=f"Компенсация выезда по заказу #{order.public_number}",
                    )
                )
            if bonus > ZERO:
                session.add(
                    PayoutLedgerEntry(
                        executor_id=asgn.executor_id,
                        order_id=order.id,
                        entry_type=LedgerEntryType.URGENCY_BONUS.value,
                        amount=bonus,
                        status=LedgerEntryStatus.ACCRUED.value,
                        comment=f"Бонус за срочность по заказу #{order.public_number}",
                    )
                )
            profile = await session.get(ExecutorProfile, asgn.executor_id)
            if profile:
                profile.completed_orders_count += 1
            exec_user = await session.get(User, asgn.executor_id)
            if exec_user:
                notified.append((exec_user, earn + comp + bonus))

        return notified

    async def take_order_atomic(
        self,
        *,
        order_id: uuid.UUID,
        executor_user_id: uuid.UUID,
        assigned_by_admin_id: uuid.UUID | None = None,
    ) -> tuple[Order, OrderAssignment]:
        """Атомарный захват заказа кнопкой «Беру» (FOR UPDATE + asyncio.Lock)."""
        async with self._order_locks[order_id], self.session_factory() as session:
            stmt = select(Order).where(Order.id == order_id)
            if not self.settings.is_sqlite:
                stmt = stmt.with_for_update()
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                raise NotFoundError("Заказ не найден")

            if order.status not in (
                OrderStatus.SEARCHING_EXECUTOR.value,
                OrderStatus.PARTIALLY_ASSIGNED.value,
            ):
                raise OrderAlreadyTakenError("Заказ уже забрал другой исполнитель или он закрыт")

            profile = await session.get(ExecutorProfile, executor_user_id)
            exec_user = await session.get(User, executor_user_id)
            if exec_user is None or exec_user.is_blocked:
                raise PermissionDeniedError("Ваш профиль исполнителя не активирован")

            # Владелец / админ / бригадир может брать заказы без ручной самоверификации
            if profile is None and exec_user.role in (
                UserRole.OWNER.value,
                UserRole.ADMIN.value,
                UserRole.FOREMAN.value,
            ):
                profile = ExecutorProfile(
                    user_id=exec_user.id,
                    status=ExecutorStatus.ACTIVE.value,
                    verified_at=utcnow(),
                )
                session.add(profile)
                await session.flush()

            if profile is None or profile.status != ExecutorStatus.ACTIVE.value:
                raise PermissionDeniedError(
                    "Ваш профиль исполнителя ещё не активирован. "
                    "Откройте личку с ботом и нажмите «👷 Стать исполнителем», "
                    "затем дождитесь подтверждения диспетчера."
                )

            # Проверка допуска к крышам и срока страховки
            if order.service_type == ServiceType.ROOF_CLEANING.value:
                today = utcnow().date()
                if not profile.roof_certified or (
                    profile.insurance_valid_until and profile.insurance_valid_until < today
                ):
                    raise PermissionDeniedError(
                        "Для расчистки крыш нужен активный допуск и действующая страховка"
                    )

            # Проверка лимита одновременных заказов
            current_exec_orders = (
                await session.execute(
                    select(func.count(OrderAssignment.id)).where(
                        OrderAssignment.executor_id == executor_user_id,
                        OrderAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                    )
                )
            ).scalar() or 0
            if assigned_by_admin_id is None and current_exec_orders >= profile.max_active_orders:
                raise PermissionDeniedError(
                    f"Достигнут лимит активных заказов ({profile.max_active_orders})"
                )

            existing_active = (
                await session.execute(
                    select(OrderAssignment).where(
                        OrderAssignment.order_id == order.id,
                        OrderAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                    )
                )
            ).scalars().all()
            if any(a.executor_id == executor_user_id for a in existing_active):
                raise OrderAlreadyTakenError("Вы уже назначены на этот заказ")
            if len(existing_active) >= order.required_executors:
                raise OrderAlreadyTakenError("Все слоты на этот заказ уже заняты")

            snap = order.pricing_snapshot or {}
            share_pct = (
                profile.share_pct_override
                if profile.share_pct_override is not None
                else to_decimal(snap.get("executor_share_pct", 65))
            )
            assignment = OrderAssignment(
                order_id=order.id,
                executor_id=executor_user_id,
                role_in_order=(
                    AssignmentRole.PRIMARY.value
                    if len(existing_active) == 0
                    else AssignmentRole.HELPER.value
                ),
                status=AssignmentStatus.ASSIGNED.value,
                executor_share_pct=share_pct,
            )
            session.add(assignment)

            new_count = len(existing_active) + 1
            old_status = order.status
            new_status = (
                OrderStatus.ASSIGNED
                if new_count >= order.required_executors
                else OrderStatus.PARTIALLY_ASSIGNED
            )
            assert_order_transition(OrderStatus(old_status), new_status)
            order.status = new_status.value

            await self._log_status(
                session,
                order=order,
                from_status=old_status,
                to_status=new_status.value,
                actor_user_id=executor_user_id,
                actor_admin_id=assigned_by_admin_id,
                reason=f"Исполнитель {exec_user.display_name} взял заказ",
            )

            # Список имён для обновления карточки в общем чате
            taken_users = [
                await session.get(User, a.executor_id) for a in existing_active
            ]
            taken_names = [u.display_name for u in taken_users if u is not None] + [
                exec_user.display_name
            ]
            client = await session.get(User, order.client_id)

            await session.commit()
            await session.refresh(order)
            await session.refresh(assignment)

        await self._publish_or_update_dispatch_card(
            order.id,
            taken_names=taken_names,
            show_take_button=(new_status == OrderStatus.PARTIALLY_ASSIGNED),
        )

        # В личку исполнителю — ТОЧНЫЙ адрес и управление заказом (анти-увод A8)
        contact_line = "💬 Связь с клиентом: через прокси-чат бота (кнопка ниже)"
        # СТРОГО ПО ПРАВИЛАМ MAX: НИКОГДА не передаём client.bot_phone!
        if (
            client is not None
            and order.contact_mode == ContactMode.DIRECT_PHONE.value
            and client.shared_contact_phone
        ):
            contact_line = f"📞 Телефон клиента (с согласия 152-ФЗ): {client.shared_contact_phone}"

        coords_line = (
            f"🗺 Координаты: `{order.lat:.5f}, {order.lon:.5f}`"
            if order.lat is not None and order.lon is not None
            else ""
        )
        comment_line = f"📝 Комментарий: {order.client_comment}" if order.client_comment else ""
        dm_lines = [
            f"🎯 **Заказ #{order.public_number} закреплён за вами!**",
            f"🛠 Услуга: {SERVICE_LABELS.get(ServiceType(order.service_type), order.service_type)}",
            f"🏠 **Точный адрес:** {order.address_text}",
            coords_line,
            comment_line,
            contact_line,
            f"💵 Сумма заказа: {order.final_total:.0f} ₽ (аванс оплачен: {order.paid_amount:.0f} ₽)",
            "",
            "Нажмите «Выехал», как только отправитесь на объект (норматив — 15 минут).",
        ]
        await self._notify_max_user(exec_user.max_user_id, OutgoingMessage(
                text="\n".join(line for line in dm_lines if line != ""),
                keyboard=self._executor_order_keyboard(order),
            ),
        )

        # Уведомление клиенту
        if client is not None and order.status == OrderStatus.ASSIGNED.value:
            await self._notify_max_user(client.max_user_id, OutgoingMessage(
                    text=(
                        f"👷 **Исполнитель найден по заказу #{order.public_number}!**\n"
                        f"К вам назначен: **{exec_user.display_name}** (⭐ {profile.rating}).\n"
                        "Сообщим, как только исполнитель выедет."
                    ),
                    keyboard=InlineKeyboard(
                        rows=[
                            [
                                InlineButton(
                                    type="callback",
                                    text="💬 Написать исполнителю",
                                    payload=f"ord:msg:{order.id}",
                                ),
                                InlineButton(
                                    type="callback",
                                    text="❌ Отменить",
                                    payload=f"ord:cancel:{order.id}",
                                ),
                            ]
                        ]
                    ),
                ),
            )

        return order, assignment

    def _executor_order_keyboard(self, order: Order) -> InlineKeyboard:
        st = OrderStatus(order.status)
        rows: list[list[InlineButton]] = []
        if st == OrderStatus.ASSIGNED:
            rows.append(
                [
                    InlineButton(
                        type="callback", text="🚗 Выехал", payload=f"ord:st:{order.id}:en_route"
                    )
                ]
            )
            rows.append(
                [
                    InlineButton(
                        type="callback", text="💬 Написать клиенту", payload=f"ord:msg:{order.id}"
                    ),
                    InlineButton(
                        type="callback", text="🚫 Отказаться", payload=f"ord:drop:{order.id}"
                    ),
                ]
            )
        elif st == OrderStatus.EN_ROUTE:
            rows.append(
                [
                    InlineButton(
                        type="callback", text="📍 На месте", payload=f"ord:st:{order.id}:arrived"
                    )
                ]
            )
            rows.append(
                [
                    InlineButton(
                        type="callback", text="💬 Написать клиенту", payload=f"ord:msg:{order.id}"
                    )
                ]
            )
        elif st == OrderStatus.ARRIVED:
            rows.append(
                [
                    InlineButton(
                        type="callback",
                        text="📸 Загрузить фото ДО",
                        payload=f"ord:photo:{order.id}:before",
                    ),
                    InlineButton(
                        type="callback",
                        text="▶️ Начать работу",
                        payload=f"ord:st:{order.id}:in_progress",
                    ),
                ]
            )
            rows.append(
                [
                    InlineButton(
                        type="callback",
                        text="💰 Расхождение >20%",
                        payload=f"ord:askadj:{order.id}",
                    ),
                    InlineButton(
                        type="callback", text="💬 Клиенту", payload=f"ord:msg:{order.id}"
                    ),
                ]
            )
        elif st == OrderStatus.IN_PROGRESS:
            rows.append(
                [
                    InlineButton(
                        type="callback",
                        text="📸 Загрузить фото ПОСЛЕ",
                        payload=f"ord:photo:{order.id}:after",
                    ),
                    InlineButton(
                        type="callback",
                        text="✅ Завершить работу",
                        payload=f"ord:st:{order.id}:awaiting_acceptance",
                    ),
                ]
            )
            rows.append(
                [
                    InlineButton(
                        type="callback", text="💬 Написать клиенту", payload=f"ord:msg:{order.id}"
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineButton(
                        type="callback", text="💬 Написать клиенту", payload=f"ord:msg:{order.id}"
                    )
                ]
            )
        return InlineKeyboard(rows=rows)

    async def upload_order_media(
        self,
        *,
        order_id: uuid.UUID,
        uploader_user_id: uuid.UUID,
        stage: MediaStage,
        data: bytes,
        filename: str,
        mime_type: str = "image/jpeg",
        max_photo_id: int | None = None,
        max_token: str | None = None,
    ) -> OrderMedia:
        if not data:
            raise ValidationAppError("Файл изображения пуст")
        if len(data) > 20 * 1024 * 1024:
            raise ValidationAppError("Размер фото превышает 20 МБ")

        # Проверяем права до загрузки файла в хранилище: идентификатор заказа не даёт доступ.
        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            uploader = await session.get(User, uploader_user_id)
            if uploader is None or uploader.is_blocked:
                raise PermissionDeniedError("Доступ к загрузке фото запрещён")
            assignment = (await session.execute(
                select(OrderAssignment).where(
                    OrderAssignment.order_id == order_id,
                    OrderAssignment.executor_id == uploader_user_id,
                    OrderAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                )
            )).scalar_one_or_none()
            is_client = order.client_id == uploader_user_id
            is_staff = uploader.role in (UserRole.FOREMAN.value, UserRole.ADMIN.value, UserRole.OWNER.value)
            if stage == MediaStage.CLIENT_PROBLEM:
                allowed = is_client
            elif stage == MediaStage.DISPUTE:
                allowed = is_client or assignment is not None or is_staff
            else:
                allowed = assignment is not None or is_staff
            if not allowed:
                raise PermissionDeniedError("У вас нет доступа к фото этого заказа")

        safe_name = "".join(ch for ch in filename if ch.isalnum() or ch in "._-") or "photo.jpg"
        storage_key = f"orders/{order_id}/{stage.value}/{uuid.uuid4().hex[:10]}_{safe_name}"
        stored = await self.storage.put_object(
            key=storage_key, data=data, content_type=mime_type
        )

        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            media = OrderMedia(
                order_id=order.id,
                uploaded_by=uploader_user_id,
                stage=stage.value,
                storage_key=stored.storage_key,
                max_photo_id=max_photo_id,
                max_token=max_token,
                mime_type=stored.mime_type,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
            )
            session.add(media)
            await session.commit()
            await session.refresh(media)
            return media

    async def transition_executor_status(
        self,
        *,
        order_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        target_status: OrderStatus,
    ) -> Order:
        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            actor = await session.get(User, actor_user_id)
            if actor is None:
                raise NotFoundError("Пользователь не найден")

            assignment = (
                await session.execute(
                    select(OrderAssignment).where(
                        OrderAssignment.order_id == order.id,
                        OrderAssignment.executor_id == actor_user_id,
                        OrderAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                    )
                )
            ).scalar_one_or_none()
            if assignment is None and actor.role not in (
                UserRole.FOREMAN.value,
                UserRole.ADMIN.value,
                UserRole.OWNER.value,
            ):
                raise PermissionDeniedError("Вы не назначены на этот заказ")

            assert_order_transition(OrderStatus(order.status), target_status)

            # Обязательная проверка фото «ДО» перед началом работ (AC-1.4)
            if target_status == OrderStatus.IN_PROGRESS:
                pending_adj = (
                    await session.execute(
                        select(PriceAdjustment).where(
                            PriceAdjustment.order_id == order.id,
                            PriceAdjustment.status == PriceAdjustmentStatus.PENDING.value,
                        )
                    )
                ).scalar_one_or_none()
                if pending_adj is not None:
                    raise InvalidStateTransitionError(
                        "Ожидается ответ клиента на корректировку цены"
                    )
                before_cnt = (
                    await session.execute(
                        select(func.count(OrderMedia.id)).where(
                            OrderMedia.order_id == order.id,
                            OrderMedia.stage == MediaStage.BEFORE.value,
                        )
                    )
                ).scalar() or 0
                if before_cnt < 1:
                    raise MediaRequiredError(
                        "Перед началом работ необходимо загрузить минимум 1 фото «ДО»"
                    )

            # Обязательная проверка фото «ПОСЛЕ» перед завершением (AC-1.4)
            if target_status == OrderStatus.AWAITING_ACCEPTANCE:
                after_cnt = (
                    await session.execute(
                        select(func.count(OrderMedia.id)).where(
                            OrderMedia.order_id == order.id,
                            OrderMedia.stage == MediaStage.AFTER.value,
                        )
                    )
                ).scalar() or 0
                if after_cnt < 1:
                    raise MediaRequiredError(
                        "Перед завершением работ необходимо загрузить минимум 1 фото «ПОСЛЕ»"
                    )

            old_status = order.status
            order.status = target_status.value
            now = utcnow()
            if assignment:
                if target_status == OrderStatus.EN_ROUTE:
                    assignment.status = AssignmentStatus.EN_ROUTE.value
                    assignment.en_route_at = now
                elif target_status == OrderStatus.ARRIVED:
                    assignment.status = AssignmentStatus.ARRIVED.value
                    assignment.arrived_at = now
                elif target_status == OrderStatus.IN_PROGRESS:
                    assignment.status = AssignmentStatus.IN_PROGRESS.value
                    assignment.started_at = now
                elif target_status == OrderStatus.AWAITING_ACCEPTANCE:
                    assignment.finished_at = now

            await self._log_status(
                session,
                order=order,
                from_status=old_status,
                to_status=target_status.value,
                actor_user_id=actor_user_id,
            )
            client = await session.get(User, order.client_id)
            before_media = (
                await session.execute(
                    select(OrderMedia).where(
                        OrderMedia.order_id == order.id,
                        OrderMedia.stage == MediaStage.BEFORE.value,
                    )
                )
            ).scalars().all()
            after_media = (
                await session.execute(
                    select(OrderMedia).where(
                        OrderMedia.order_id == order.id,
                        OrderMedia.stage == MediaStage.AFTER.value,
                    )
                )
            ).scalars().all()
            await session.commit()
            await session.refresh(order)

        # Обновляем панель управления исполнителю
        await self._notify_max_user(actor.max_user_id, OutgoingMessage(
                text=f"📌 Статус заказа **#{order.public_number}** обновлён: **{STATUS_LABELS[target_status]}**.",
                keyboard=(
                    self._executor_order_keyboard(order)
                    if target_status != OrderStatus.AWAITING_ACCEPTANCE
                    else None
                ),
            ),
        )

        # Уведомляем клиента
        if client is not None:
            if target_status == OrderStatus.EN_ROUTE:
                await self._notify_max_user(client.max_user_id, OutgoingMessage(
                        text=f"🚗 Исполнитель **выехал** по заказу **#{order.public_number}**! Ориентировочное прибытие: 15–30 минут."
                    ),
                )
            elif target_status == OrderStatus.ARRIVED:
                await self._notify_max_user(client.max_user_id, OutgoingMessage(
                        text=f"📍 Исполнитель **прибыл на место** по заказу **#{order.public_number}**. Скоро начнёт работу и пришлёт фото «ДО»."
                    ),
                )
            elif target_status == OrderStatus.IN_PROGRESS:
                await self._notify_max_user(client.max_user_id, OutgoingMessage(
                        text=(
                            f"▶️ Работы по заказу **#{order.public_number}** начались!\n"
                            f"📸 Загружено фото «ДО»: {len(before_media)} шт."
                        ),
                        extra_attachments=[
                            {
                                "type": "image",
                                "payload": {"url": f"/api/v1/media/file/{m.id}"},
                            }
                            for m in before_media[:3]
                        ],
                    ),
                )
            elif target_status == OrderStatus.AWAITING_ACCEPTANCE:
                remainder = max(ZERO, order.final_total - order.paid_amount)
                await self._notify_max_user(client.max_user_id, OutgoingMessage(
                        text=(
                            f"✅ **Работы по заказу #{order.public_number} завершены!**\n"
                            f"📸 Фотоотчёт «ПОСЛЕ» ({len(after_media)} шт.) прикреплён.\n"
                            f"Итого к оплате остаток: **{remainder:.0f} ₽**.\n\n"
                            "Пожалуйста, проверьте результат (при отсутствии ответа автоприёмка через 24 ч):"
                        ),
                        extra_attachments=[
                            {
                                "type": "image",
                                "payload": {"url": f"/api/v1/media/file/{m.id}"},
                            }
                            for m in after_media[:3]
                        ],
                        keyboard=InlineKeyboard(
                            rows=[
                                [
                                    InlineButton(
                                        type="callback",
                                        text="✅ Принять работу",
                                        payload=f"acc:ok:{order.id}",
                                    ),
                                    InlineButton(
                                        type="callback",
                                        text="⚠️ Есть замечания",
                                        payload=f"acc:disp:{order.id}",
                                    ),
                                ]
                            ]
                        ),
                    ),
                )

        return order

    async def propose_price_adjustment(
        self,
        *,
        order_id: uuid.UUID,
        proposer_user_id: uuid.UUID,
        new_total: Decimal,
        reason: str,
        actor_admin_id: uuid.UUID | None = None,
    ) -> PriceAdjustment:
        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            proposer = await session.get(User, proposer_user_id)
            if proposer is None:
                raise NotFoundError("Инициатор не найден")
            if actor_admin_id is None and proposer.role not in (
                UserRole.FOREMAN.value,
                UserRole.ADMIN.value,
                UserRole.OWNER.value,
            ):
                raise PermissionDeniedError("Корректировку цены >20% может инициировать только бригадир или админ")

            assert_order_transition(OrderStatus(order.status), OrderStatus.PRICE_ADJUSTMENT_PENDING)
            old_total = order.final_total
            new_total_dec = to_decimal(new_total)
            if new_total_dec <= ZERO:
                raise ValidationAppError("Новая сумма должна быть больше нуля")
            delta_pct = (
                ((new_total_dec - old_total) / old_total * Decimal("100")).quantize(Decimal("0.01"))
                if old_total > ZERO
                else ZERO
            )
            adj = PriceAdjustment(
                order_id=order.id,
                proposed_by=proposer.id,
                old_total=old_total,
                new_total=new_total_dec,
                delta_pct=delta_pct,
                reason=reason,
                status=PriceAdjustmentStatus.PENDING.value,
                expires_at=utcnow() + timedelta(minutes=30),
            )
            session.add(adj)
            old_st = order.status
            order.status = OrderStatus.PRICE_ADJUSTMENT_PENDING.value
            await self._log_status(
                session,
                order=order,
                from_status=old_st,
                to_status=order.status,
                actor_user_id=proposer.id,
                actor_admin_id=actor_admin_id,
                reason=f"Предложена корректировка цены {old_total:.0f} -> {new_total_dec:.0f} ₽ ({delta_pct:+.1f}%): {reason}",
            )
            client = await session.get(User, order.client_id)
            await session.commit()
            await session.refresh(adj)

        if client is not None:
            await self._notify_max_user(client.max_user_id, OutgoingMessage(
                    text=(
                        f"💰 **Уточнение стоимости по заказу #{order.public_number}**\n"
                        f"Бригадир зафиксировал расхождение объёма на месте:\n"
                        f"• Причина: {reason}\n"
                        f"• Было ориентировочно: **{old_total:.0f} ₽**\n"
                        f"• Новая стоимость: **{new_total_dec:.0f} ₽** ({delta_pct:+.1f}%)\n\n"
                        "Доплата применяется **только с вашего согласия**. Выберите действие:"
                    ),
                    keyboard=InlineKeyboard(
                        rows=[
                            [
                                InlineButton(
                                    type="callback",
                                    text=f"✅ Согласен ({new_total_dec:.0f} ₽)",
                                    payload=f"adj:ok:{adj.id}",
                                )
                            ],
                            [
                                InlineButton(
                                    type="callback",
                                    text="🔄 В старом бюджете",
                                    payload=f"adj:keep:{adj.id}",
                                ),
                                InlineButton(
                                    type="callback",
                                    text="❌ Отменить заказ",
                                    payload=f"adj:cancel:{adj.id}",
                                ),
                            ],
                        ]
                    ),
                ),
            )
        return adj

    async def respond_price_adjustment(
        self,
        *,
        adjustment_id: uuid.UUID,
        client_user_id: uuid.UUID,
        decision: str,  # approve | keep_old | cancel
    ) -> Order:
        if decision == "cancel":
            async with self.session_factory() as session:
                adj = await session.get(PriceAdjustment, adjustment_id)
                if adj is None:
                    raise NotFoundError("Запрос корректировки не найден")
                adj.status = PriceAdjustmentStatus.REJECTED.value
                adj.client_responded_at = utcnow()
                order_id = adj.order_id
                await session.commit()
            return await self.cancel_order(
                order_id=order_id,
                by_service=False,
                reason="Отказ клиента от скорректированной цены на месте",
                actor_user_id=client_user_id,
            )

        executors_to_notify: list[User] = []
        async with self.session_factory() as session:
            adj = await session.get(PriceAdjustment, adjustment_id)
            if adj is None or adj.status != PriceAdjustmentStatus.PENDING.value:
                raise InvalidStateTransitionError("Корректировка уже обработана или не найдена")
            order = await session.get(Order, adj.order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            if order.client_id != client_user_id:
                raise PermissionDeniedError("Только клиент заказа может ответить на корректировку")

            adj.client_responded_at = utcnow()
            old_st = order.status
            if decision == "approve":
                adj.status = PriceAdjustmentStatus.APPROVED.value
                order.final_total = adj.new_total
                reason_txt = f"Клиент согласился с новой ценой {adj.new_total:.0f} ₽"
            else:
                adj.status = PriceAdjustmentStatus.REJECTED.value
                reason_txt = "Клиент отклонил доплату, работа в рамках исходного бюджета"

            assert_order_transition(OrderStatus(old_st), OrderStatus.ARRIVED)
            order.status = OrderStatus.ARRIVED.value
            await self._log_status(
                session,
                order=order,
                from_status=old_st,
                to_status=order.status,
                actor_user_id=client_user_id,
                reason=reason_txt,
            )
            asgns = (
                await session.execute(
                    select(OrderAssignment).where(
                        OrderAssignment.order_id == order.id,
                        OrderAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                    )
                )
            ).scalars().all()
            for a in asgns:
                u = await session.get(User, a.executor_id)
                if u:
                    executors_to_notify.append(u)
            await session.commit()
            await session.refresh(order)

        for exec_user in executors_to_notify:
            await self._notify_max_user(exec_user.max_user_id, OutgoingMessage(
                    text=(
                        f"📢 Ответ клиента по заказу **#{order.public_number}**: {reason_txt}.\n"
                        f"Актуальный итог заказа: **{order.final_total:.0f} ₽**. "
                        "Загрузите фото «ДО» и нажмите «Начать работу»."
                    ),
                    keyboard=self._executor_order_keyboard(order),
                ),
            )
        return order

    async def accept_order_by_client(
        self,
        *,
        order_id: uuid.UUID,
        client_user_id: uuid.UUID | None = None,
        auto_accepted: bool = False,
    ) -> tuple[Order, Payment | None]:
        executors_to_notify: list[tuple[User, Decimal]] = []
        client: User | None = None
        final_payment: Payment | None = None

        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            if client_user_id is not None and order.client_id != client_user_id:
                raise PermissionDeniedError("Только клиент заказа может принять работу")
            client = await session.get(User, order.client_id)
            remainder = max(ZERO, (order.final_total - order.paid_amount).quantize(Decimal("0.01")))
            old_st = order.status

            if remainder > ZERO:
                assert_order_transition(OrderStatus(old_st), OrderStatus.AWAITING_FINAL_PAYMENT)
                order.status = OrderStatus.AWAITING_FINAL_PAYMENT.value
                order.payment_status = OrderPaymentStatus.FINAL_PENDING.value
                await self._log_status(
                    session,
                    order=order,
                    from_status=old_st,
                    to_status=order.status,
                    actor_user_id=client_user_id,
                    reason="Автоприёмка (24ч)" if auto_accepted else "Клиент принял работу",
                )

                pay_id = uuid.uuid4()
                idem_key = uuid.uuid4()
                return_url = (
                    f"{self.settings.public_base_url.rstrip('/')}/pay/return?order={order.public_number}"
                    if self.settings.public_base_url
                    else f"/pay/return?order={order.public_number}"
                )
                svc_label = SERVICE_LABELS.get(ServiceType(order.service_type), order.service_type)
                pay_res = await self.payment_provider.create_payment(
                    CreatePaymentCommand(
                        internal_payment_id=pay_id,
                        idempotency_key=idem_key,
                        amount=remainder,
                        description=f"Доплата по заказу #{order.public_number}: {svc_label}",
                        return_url=return_url,
                        metadata={"order_id": str(order.id), "purpose": PaymentPurpose.FINAL.value},
                        customer_email=client.email if client else None,
                        customer_phone=(
                            (client.shared_contact_phone or client.bot_phone) if client else None
                        ),
                        receipt_items=[
                            ReceiptItem(
                                description=f"Окончательный расчёт: {svc_label} (#{order.public_number})",
                                amount=remainder,
                                payment_mode="full_payment",
                            )
                        ],
                        prepayment_offset=order.paid_amount,
                    )
                )
                final_payment = Payment(
                    id=pay_id,
                    order_id=order.id,
                    purpose=PaymentPurpose.FINAL.value,
                    provider=pay_res.provider,
                    provider_payment_id=pay_res.provider_payment_id,
                    idempotency_key=idem_key,
                    amount=remainder,
                    currency="RUB",
                    status=PaymentStatus.PENDING.value,
                    confirmation_url=pay_res.confirmation_url,
                    receipt_status="pending",
                    expires_at=utcnow() + timedelta(hours=24),
                    raw_response=pay_res.raw_response,
                )
                session.add(final_payment)
            else:
                assert_order_transition(OrderStatus(old_st), OrderStatus.COMPLETED)
                order.status = OrderStatus.COMPLETED.value
                order.payment_status = OrderPaymentStatus.FULLY_PAID.value
                order.completed_at = utcnow()
                await self._log_status(
                    session,
                    order=order,
                    from_status=old_st,
                    to_status=order.status,
                    actor_user_id=client_user_id,
                    reason="Приёмка без доплаты — заказ завершён",
                )
                executors_to_notify = await self._accrue_order_earnings(session, order)

            await session.commit()
            await session.refresh(order)
            if final_payment:
                await session.refresh(final_payment)

        if client is not None and final_payment is not None:
            await self._notify_max_user(client.max_user_id, OutgoingMessage(
                    text=(
                        f"💳 **Оплата остатка по заказу #{order.public_number}**\n"
                        f"Итого: {order.final_total:.0f} ₽ | Оплачено авансом: {order.paid_amount:.0f} ₽\n"
                        f"К доплате через СБП: **{final_payment.amount:.0f} ₽**"
                    ),
                    keyboard=InlineKeyboard(
                        rows=[
                            [
                                InlineButton(
                                    type="link",
                                    text=f"💳 Оплатить {final_payment.amount:.0f} ₽ (СБП)",
                                    url=final_payment.confirmation_url or "/mock-pay",
                                )
                            ]
                        ]
                    ),
                ),
            )
        for exec_user, total_earned in executors_to_notify:
            await self._notify_max_user(exec_user.max_user_id, OutgoingMessage(
                    text=f"💰 Заказ **#{order.public_number}** закрыт! Начислено **+{total_earned:.0f} ₽**."
                ),
            )
        return order, final_payment

    async def rate_order(
        self, *, order_id: uuid.UUID, client_user_id: uuid.UUID, rating: int
    ) -> Order:
        if rating < 1 or rating > 5:
            raise ValidationAppError("Оценка должна быть от 1 до 5")
        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            if order.client_id != client_user_id:
                raise PermissionDeniedError("Оценить заказ может только клиент")
            order.client_rating = rating
            asgns = (
                await session.execute(
                    select(OrderAssignment).where(OrderAssignment.order_id == order.id)
                )
            ).scalars().all()
            for a in asgns:
                prof = await session.get(ExecutorProfile, a.executor_id)
                if prof:
                    total_score = prof.rating * Decimal(prof.ratings_count) + Decimal(rating)
                    prof.ratings_count += 1
                    prof.rating = (total_score / Decimal(prof.ratings_count)).quantize(
                        Decimal("0.01")
                    )
            await session.commit()
            await session.refresh(order)
            return order

    async def open_dispute(
        self, *, order_id: uuid.UUID, opener_user_id: uuid.UUID, reason: str
    ) -> Dispute:
        foremen: list[User] = []
        async with self.session_factory() as session:
            stmt = select(Order).where(Order.id == order_id)
            if not self.settings.is_sqlite:
                stmt = stmt.with_for_update()
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                raise NotFoundError("Заказ не найден")
            if order.client_id != opener_user_id:
                raise PermissionDeniedError("Открыть спор может только клиент заказа")
            assert_order_transition(OrderStatus(order.status), OrderStatus.DISPUTED)
            old_st = order.status
            order.status = OrderStatus.DISPUTED.value
            dispute = Dispute(
                order_id=order.id,
                opened_by=opener_user_id,
                reason=reason,
                status=DisputeStatus.OPEN.value,
            )
            session.add(dispute)
            await self._log_status(
                session,
                order=order,
                from_status=old_st,
                to_status=order.status,
                actor_user_id=opener_user_id,
                reason=f"Открыт спор: {reason}",
            )
            foremen = list(
                (
                    await session.execute(
                        select(User).where(User.role == UserRole.FOREMAN.value)
                    )
                ).scalars().all()
            )
            await session.commit()
            await session.refresh(dispute)

        for foreman in foremen:
            await self._notify_max_user(foreman.max_user_id, OutgoingMessage(
                    text=(
                        f"⚠️ **Открыт спор по заказу #{order.public_number}!**\n"
                        f"Причина клиента: {reason}\n"
                        "Откройте веб-админку или выберите действие:"
                    ),
                    keyboard=InlineKeyboard(
                        rows=[
                            [
                                InlineButton(
                                    type="callback",
                                    text="🔄 Вернуть на доработку",
                                    payload=f"disp:rework:{dispute.id}",
                                )
                            ]
                        ]
                    ),
                ),
            )
        return dispute

    async def resolve_dispute(
        self,
        *,
        dispute_id: uuid.UUID,
        resolution: DisputeStatus,
        note: str,
        admin_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        discount_amount: Decimal = ZERO,
        refund_amount: Decimal = ZERO,
    ) -> Dispute:
        allowed_resolutions = {
            DisputeStatus.RESOLVED_REWORK,
            DisputeStatus.RESOLVED_REFUND,
            DisputeStatus.RESOLVED_DISCOUNT,
            DisputeStatus.REJECTED,
        }
        if resolution not in allowed_resolutions or discount_amount < ZERO or refund_amount < ZERO:
            raise ValidationAppError("Некорректное решение или сумма по спору")
        async with self.session_factory() as session:
            if admin_id is not None:
                admin = await session.get(AdminAccount, admin_id)
                role = admin.role if admin and admin.is_active else None
            elif actor_user_id is not None:
                actor = await session.get(User, actor_user_id)
                role = actor.role if actor and not actor.is_blocked else None
            else:
                role = None
            if role not in (AdminRole.FOREMAN.value, AdminRole.ADMIN.value, AdminRole.OWNER.value):
                raise PermissionDeniedError("Решение спора доступно только диспетчеру")
            if role == AdminRole.FOREMAN.value and (
                resolution in (DisputeStatus.RESOLVED_REFUND, DisputeStatus.RESOLVED_DISCOUNT)
                or discount_amount > ZERO or refund_amount > ZERO
            ):
                raise PermissionDeniedError("Денежное решение доступно только администратору")
            stmt = select(Dispute).where(Dispute.id == dispute_id)
            if not self.settings.is_sqlite:
                stmt = stmt.with_for_update()
            dispute = (await session.execute(stmt)).scalar_one_or_none()
            if dispute is None:
                raise NotFoundError("Спор не найден")
            if dispute.status not in (DisputeStatus.OPEN.value, DisputeStatus.IN_REVIEW.value):
                raise InvalidStateTransitionError("Спор уже рассмотрен")
            order = await session.get(Order, dispute.order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")

            dispute.status = resolution.value
            dispute.resolution_note = note
            dispute.discount_amount = to_decimal(discount_amount)
            dispute.refund_amount = to_decimal(refund_amount)
            dispute.assigned_admin = admin_id
            dispute.resolved_at = utcnow()
            old_st = order.status

            if resolution == DisputeStatus.RESOLVED_REWORK:
                assert_order_transition(OrderStatus(old_st), OrderStatus.IN_PROGRESS)
                order.status = OrderStatus.IN_PROGRESS.value
            elif resolution == DisputeStatus.RESOLVED_REFUND:
                assert_order_transition(OrderStatus(old_st), OrderStatus.CANCELED_BY_SERVICE)
                order.status = OrderStatus.CANCELED_BY_SERVICE.value
                await self._execute_refund_in_session(
                    session,
                    order=order,
                    amount=to_decimal(refund_amount) or order.paid_amount,
                    reason=f"Возврат по спору: {note}",
                    admin_id=admin_id,
                )
            elif resolution in (DisputeStatus.RESOLVED_DISCOUNT, DisputeStatus.REJECTED):
                if discount_amount > ZERO:
                    order.final_total = max(ZERO, order.final_total - to_decimal(discount_amount))
                if refund_amount > ZERO:
                    await self._execute_refund_in_session(
                        session,
                        order=order,
                        amount=to_decimal(refund_amount),
                        reason=f"Частичный возврат по спору: {note}",
                        admin_id=admin_id,
                    )
                if order.paid_amount >= order.final_total:
                    assert_order_transition(OrderStatus(old_st), OrderStatus.COMPLETED)
                    order.status = OrderStatus.COMPLETED.value
                    order.payment_status = OrderPaymentStatus.FULLY_PAID.value
                    order.completed_at = utcnow()
                    await self._accrue_order_earnings(session, order)
                else:
                    assert_order_transition(OrderStatus(old_st), OrderStatus.AWAITING_FINAL_PAYMENT)
                    order.status = OrderStatus.AWAITING_FINAL_PAYMENT.value

            await self._log_status(
                session,
                order=order,
                from_status=old_st,
                to_status=order.status,
                actor_user_id=actor_user_id,
                actor_admin_id=admin_id,
                reason=f"Решение спора ({resolution.value}): {note}",
            )
            client = await session.get(User, order.client_id)
            await session.commit()
            await session.refresh(dispute)

        if client:
            await self._notify_max_user(client.max_user_id, OutgoingMessage(
                    text=(
                        f"⚖️ **Спор по заказу #{order.public_number} рассмотрен.**\n"
                        f"Решение: {note}\n"
                        f"Новый статус заказа: **{STATUS_LABELS[OrderStatus(order.status)]}**."
                    )
                ),
            )
        return dispute

    async def _execute_refund_in_session(
        self,
        session: AsyncSession,
        *,
        order: Order,
        amount: Decimal,
        reason: str,
        admin_id: uuid.UUID | None = None,
    ) -> Refund | None:
        refund_amt = min(to_decimal(amount), order.paid_amount)
        if refund_amt <= ZERO:
            return None
        payment = (
            await session.execute(
                select(Payment)
                .where(
                    Payment.order_id == order.id,
                    Payment.status == PaymentStatus.SUCCEEDED.value,
                )
                .order_by(Payment.paid_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if payment is None or not payment.provider_payment_id:
            return None

        idem = uuid.uuid4()
        res = await self.payment_provider.create_refund(
            CreateRefundCommand(
                provider_payment_id=payment.provider_payment_id,
                idempotency_key=idem,
                amount=refund_amt,
                reason=reason,
            )
        )
        ref = Refund(
            payment_id=payment.id,
            order_id=order.id,
            provider_refund_id=res.provider_refund_id,
            idempotency_key=idem,
            amount=refund_amt,
            reason=reason,
            status=res.status,
            initiated_by=admin_id,
            raw_response=res.raw_response,
            succeeded_at=utcnow() if res.status == RefundStatus.SUCCEEDED.value else None,
        )
        session.add(ref)
        order.payment_status = (
            OrderPaymentStatus.REFUNDED.value
            if refund_amt >= order.paid_amount
            else OrderPaymentStatus.PARTIALLY_REFUNDED.value
        )
        return ref

    async def cancel_order(
        self,
        *,
        order_id: uuid.UUID,
        by_service: bool,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
        actor_admin_id: uuid.UUID | None = None,
    ) -> Order:
        notified_executors: list[User] = []
        client: User | None = None
        refund_amt = ZERO
        withheld_amt = ZERO

        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            target = (
                OrderStatus.CANCELED_BY_SERVICE if by_service else OrderStatus.CANCELED_BY_CLIENT
            )
            assert_order_transition(OrderStatus(order.status), target)

            snap = order.pricing_snapshot or {}
            disp_fee = to_decimal(snap.get("dispatch_fee", 0))
            refund_amt, withheld_amt = calculate_cancellation_refund(
                status=OrderStatus(order.status),
                paid_amount=order.paid_amount,
                dispatch_fee=disp_fee,
                by_service=by_service,
            )

            if refund_amt > ZERO:
                await self._execute_refund_in_session(
                    session,
                    order=order,
                    amount=refund_amt,
                    reason=reason,
                    admin_id=actor_admin_id,
                )

            # Если удержан ложный выезд — начисляем компенсацию назначенному исполнителю
            active_asgns = (
                await session.execute(
                    select(OrderAssignment).where(
                        OrderAssignment.order_id == order.id,
                        OrderAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                    )
                )
            ).scalars().all()
            for a in active_asgns:
                a.status = AssignmentStatus.REMOVED_BY_FOREMAN.value
                a.released_at = utcnow()
                a.release_reason = reason
                u = await session.get(User, a.executor_id)
                if u:
                    notified_executors.append(u)
                if withheld_amt > ZERO:
                    session.add(
                        PayoutLedgerEntry(
                            executor_id=a.executor_id,
                            order_id=order.id,
                            entry_type=LedgerEntryType.DISPATCH_COMPENSATION.value,
                            amount=(withheld_amt / Decimal(len(active_asgns))).quantize(
                                Decimal("0.01")
                            ),
                            status=LedgerEntryStatus.ACCRUED.value,
                            comment=f"Компенсация ложного выезда по отменённому заказу #{order.public_number}",
                        )
                    )

            old_st = order.status
            order.status = target.value
            await self._log_status(
                session,
                order=order,
                from_status=old_st,
                to_status=order.status,
                actor_user_id=actor_user_id,
                actor_admin_id=actor_admin_id,
                reason=f"{reason} (возврат {refund_amt:.0f} ₽, удержано {withheld_amt:.0f} ₽)",
            )
            had_dispatch_card = bool(order.dispatch_message_id)
            client = await session.get(User, order.client_id)
            await session.commit()
            await session.refresh(order)

        if had_dispatch_card:
            await self._publish_or_update_dispatch_card(
                order.id,
                show_take_button=False,
                canceled_label="Заказ отменён",
            )

        if client:
            extra = (
                f"\nВозврат: **{refund_amt:.0f} ₽**"
                + (f" (удержан выезд: {withheld_amt:.0f} ₽)" if withheld_amt > ZERO else "")
                if order.paid_amount > ZERO
                else ""
            )
            await self._notify_max_user(client.max_user_id, OutgoingMessage(
                    text=f"❌ Заказ **#{order.public_number}** отменён. Причина: {reason}.{extra}"
                ),
            )
        for u in notified_executors:
            await self._notify_max_user(u.max_user_id, OutgoingMessage(
                    text=(
                        f"❌ Заказ **#{order.public_number}** отменён ({reason})."
                        + (
                            f"\nВам начислена компенсация выезда: +{withheld_amt:.0f} ₽."
                            if withheld_amt > ZERO
                            else ""
                        )
                    )
                ),
            )
        return order

    async def unassign_executor(
        self,
        *,
        order_id: uuid.UUID,
        executor_user_id: uuid.UUID,
        reason: str,
        by_foreman: bool = False,
        actor_admin_id: uuid.UUID | None = None,
        penalty_rub: Decimal = ZERO,
    ) -> Order:
        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            asgn = (
                await session.execute(
                    select(OrderAssignment).where(
                        OrderAssignment.order_id == order.id,
                        OrderAssignment.executor_id == executor_user_id,
                        OrderAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                    )
                )
            ).scalar_one_or_none()
            if asgn is None:
                raise NotFoundError("Активное назначение исполнителя не найдено")

            asgn.status = (
                AssignmentStatus.REMOVED_BY_FOREMAN.value
                if by_foreman
                else AssignmentStatus.RELEASED_BY_EXECUTOR.value
            )
            asgn.released_at = utcnow()
            asgn.release_reason = reason

            prof = await session.get(ExecutorProfile, executor_user_id)
            if prof:
                prof.canceled_orders_count += 1
            if penalty_rub > ZERO:
                session.add(
                    PayoutLedgerEntry(
                        executor_id=executor_user_id,
                        order_id=order.id,
                        entry_type=LedgerEntryType.PENALTY.value,
                        amount=-abs(to_decimal(penalty_rub)),
                        status=LedgerEntryStatus.ACCRUED.value,
                        comment=f"Штраф за срыв заказа #{order.public_number}: {reason}",
                        created_by_admin=actor_admin_id,
                    )
                )

            old_st = order.status
            assert_order_transition(OrderStatus(old_st), OrderStatus.SEARCHING_EXECUTOR)
            order.status = OrderStatus.SEARCHING_EXECUTOR.value
            await self._log_status(
                session,
                order=order,
                from_status=old_st,
                to_status=order.status,
                actor_user_id=executor_user_id,
                actor_admin_id=actor_admin_id,
                reason=f"Исполнитель снят/отказался: {reason}",
            )
            await session.commit()
            await session.refresh(order)

        # Возвращаем кнопку «Беру» в общий чат вне транзакции
        await self._publish_or_update_dispatch_card(
            order.id, taken_names=[], show_take_button=True
        )
        return order

    async def send_proxy_message(
        self, *, order_id: uuid.UUID, sender_user_id: uuid.UUID, text: str
    ) -> OrderMessage:
        """Анонимный прокси-чат между клиентом и исполнителем (без утечки телефонов)."""
        async with self.session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise NotFoundError("Заказ не найден")
            sender = await session.get(User, sender_user_id)
            if sender is None:
                raise NotFoundError("Отправитель не найден")

            if sender.id == order.client_id:
                asgn = (
                    await session.execute(
                        select(OrderAssignment)
                        .where(
                            OrderAssignment.order_id == order.id,
                            OrderAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if asgn is None:
                    raise ValidationAppError("Исполнитель ещё не назначен на заказ")
                recipient = await session.get(User, asgn.executor_id)
                prefix = f"💬 **Сообщение от клиента (заказ #{order.public_number}):**"
            else:
                recipient = await session.get(User, order.client_id)
                prefix = f"💬 **Сообщение от исполнителя (заказ #{order.public_number}):**"

            if recipient is None:
                raise NotFoundError("Получатель не найден")

            row = OrderMessage(
                order_id=order.id,
                sender_user_id=sender.id,
                recipient_user_id=recipient.id,
                text=text,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)

        await self._notify_max_user(recipient.max_user_id, OutgoingMessage(
                text=f"{prefix}\n{text}",
                keyboard=InlineKeyboard(
                    rows=[
                        [
                            InlineButton(
                                type="callback",
                                text="↩️ Ответить",
                                payload=f"ord:msg:{order.id}",
                            )
                        ]
                    ]
                ),
            ),
        )
        return row

    async def create_payout_batch(
        self, *, executor_id: uuid.UUID, admin_id: uuid.UUID | None = None
    ) -> PayoutBatch:
        async with self.session_factory() as session:
            entries = (
                await session.execute(
                    select(PayoutLedgerEntry).where(
                        PayoutLedgerEntry.executor_id == executor_id,
                        PayoutLedgerEntry.status == LedgerEntryStatus.ACCRUED.value,
                    )
                )
            ).scalars().all()
            if not entries:
                raise ValidationAppError("Нет неначисленных/доступных к выплате позиций")
            total = sum((e.amount for e in entries), ZERO).quantize(Decimal("0.01"))
            if total <= ZERO:
                raise ValidationAppError("Итоговый баланс к выплате ≤ 0 ₽")

            batch = PayoutBatch(
                executor_id=executor_id,
                total_amount=total,
                status=PayoutBatchStatus.DRAFT.value,
            )
            session.add(batch)
            await session.flush()
            for entry in entries:
                entry.status = LedgerEntryStatus.INCLUDED_IN_BATCH.value
                entry.payout_batch_id = batch.id
            await session.commit()
            await session.refresh(batch)
            return batch

    async def mark_payout_batch_paid(
        self,
        *,
        batch_id: uuid.UUID,
        admin_id: uuid.UUID,
        payment_method_note: str,
        npd_receipt_id: str | None = None,
        npd_receipt_url: str | None = None,
    ) -> PayoutBatch:
        exec_user: User | None = None
        async with self.session_factory() as session:
            batch = await session.get(PayoutBatch, batch_id)
            if batch is None:
                raise NotFoundError("Ведомость выплаты не найдена")
            if batch.status == PayoutBatchStatus.PAID.value:
                return batch
            batch.status = PayoutBatchStatus.PAID.value
            batch.payment_method_note = payment_method_note
            batch.npd_receipt_id = npd_receipt_id
            batch.npd_receipt_url = npd_receipt_url
            batch.paid_by = admin_id
            batch.paid_at = utcnow()

            entries = (
                await session.execute(
                    select(PayoutLedgerEntry).where(PayoutLedgerEntry.payout_batch_id == batch.id)
                )
            ).scalars().all()
            for entry in entries:
                entry.status = LedgerEntryStatus.PAID.value
            exec_user = await session.get(User, batch.executor_id)
            await session.commit()
            await session.refresh(batch)

        if exec_user:
            await self._notify_max_user(exec_user.max_user_id, OutgoingMessage(
                    text=(
                        f"💸 **Проведена выплата {batch.total_amount:.0f} ₽!**\n"
                        f"Способ: {payment_method_note}"
                        + (f"\nЧек НПД: {npd_receipt_id}" if npd_receipt_id else "")
                    )
                ),
            )
        return batch

    async def run_expired_timers(self, *, force_all: bool = False) -> dict[str, int]:
        """Выполняет регламентные таймеры (оплата, волны/эскалация, автоприёмка)."""
        now = utcnow()
        counts = {"expired_unpaid": 0, "escalated_dispatch": 0, "auto_accepted": 0}

        async with self.session_factory() as session:
            # 1. Таймаут неоплаченных авансов
            unpaid_orders = (
                await session.execute(
                    select(Order).where(Order.status == OrderStatus.AWAITING_ADVANCE.value)
                )
            ).scalars().all()
            expired_msgs: list[tuple[int, int]] = []
            escalated_ids: list[tuple[uuid.UUID, int, str, Decimal]] = []
            foreman_max_ids: list[int] = []

            for order in unpaid_orders:
                pay = (
                    await session.execute(
                        select(Payment)
                        .where(
                            Payment.order_id == order.id,
                            Payment.purpose == PaymentPurpose.ADVANCE.value,
                            Payment.status == PaymentStatus.PENDING.value,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                is_expired = force_all or (
                    pay is not None and pay.expires_at is not None and pay.expires_at <= now
                )
                if is_expired:
                    old_st = order.status
                    order.status = OrderStatus.CANCELED_TIMEOUT.value
                    if pay:
                        pay.status = PaymentStatus.CANCELED.value
                    await self._log_status(
                        session,
                        order=order,
                        from_status=old_st,
                        to_status=order.status,
                        reason="Истёк срок оплаты аванса",
                    )
                    counts["expired_unpaid"] += 1
                    client = await session.get(User, order.client_id)
                    if client:
                        expired_msgs.append((client.max_user_id, order.public_number))

            # 2. Эскалация неразобранных заказов (волна + бонус + уведомление бригадиру)
            tariff = await self.get_active_tariff(session)
            waves_cfg = tariff.config_json.get("dispatch_waves", {})
            esc_sec = int(waves_cfg.get("escalate_foreman_after_sec", 600))
            wave_bonus = to_decimal(waves_cfg.get("wave3_bonus_rub", 300))

            searching = (
                await session.execute(
                    select(Order).where(Order.status == OrderStatus.SEARCHING_EXECUTOR.value)
                )
            ).scalars().all()
            foremen = list(
                (
                    await session.execute(
                        select(User).where(User.role == UserRole.FOREMAN.value)
                    )
                ).scalars().all()
            )
            foreman_max_ids = [fm.max_user_id for fm in foremen]
            for order in searching:
                age_sec = (now - order.updated_at).total_seconds()
                if (force_all or age_sec >= esc_sec) and order.dispatch_wave < 3:
                    order.dispatch_wave = 3
                    order.executor_bonus_amount = wave_bonus
                    escalated_ids.append(
                        (order.id, order.public_number, order.public_area_hint, wave_bonus)
                    )
                    counts["escalated_dispatch"] += 1

            # 3. Автоприёмка через 24 часа
            acceptance_orders = (
                await session.execute(
                    select(Order).where(Order.status == OrderStatus.AWAITING_ACCEPTANCE.value)
                )
            ).scalars().all()
            to_auto_accept: list[uuid.UUID] = []
            for order in acceptance_orders:
                age_h = (now - order.updated_at).total_seconds() / 3600.0
                if force_all or age_h >= 24.0:
                    to_auto_accept.append(order.id)

            await session.commit()

        for uid, pub_num in expired_msgs:
            await self._notify_max_user(uid, OutgoingMessage(
                    text=f"⏱ Заказ **#{pub_num}** отменён: истёк срок оплаты аванса."
                ),
            )
        for oid, pub_num, area_hint, bonus in escalated_ids:
            await self._publish_or_update_dispatch_card(oid, show_take_button=True)
            for fm_uid in foreman_max_ids:
                await self._notify_max_user(fm_uid, OutgoingMessage(
                        text=(
                            f"🚨 **Эскалация!** Заказ **#{pub_num}** "
                            f"({area_hint}) никто не взял.\n"
                            f"Автоматически добавлен бонус **+{bonus:.0f} ₽**."
                        )
                    ),
                )

        for oid in to_auto_accept:
            await self.accept_order_by_client(order_id=oid, auto_accepted=True)
            counts["auto_accepted"] += 1

        return counts

    async def trigger_weather_snowfall(self, *, zone_code: str, snow_cm: Decimal) -> int:
        """Погодный триггер для подписок (Этап 3 / симулятор)."""
        targets: list[tuple[int, str, uuid.UUID, str, str]] = []
        async with self.session_factory() as session:
            zone = (
                await session.execute(select(GeoZone).where(GeoZone.code == zone_code))
            ).scalar_one_or_none()
            if zone is None:
                raise NotFoundError(f"Геозона {zone_code} не найдена")
            subs = (
                await session.execute(
                    select(Subscription).where(
                        Subscription.geo_zone_id == zone.id,
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.snow_trigger_cm <= snow_cm,
                    )
                )
            ).scalars().all()
            for sub in subs:
                client = await session.get(User, sub.client_id)
                if client:
                    targets.append(
                        (
                            client.max_user_id,
                            zone.name,
                            sub.id,
                            sub.preferred_time_window,
                            sub.address_text,
                        )
                    )
        for max_uid, zname, sub_id, window, addr in targets:
            await self._notify_max_user(max_uid, OutgoingMessage(
                    text=(
                        f"❄️ **Прогноз снегопада ({zname}): ожидается {snow_cm:.0f} см!**\n"
                        f"По вашей подписке планируем выезд к **{window}** "
                        f"по адресу: {addr}.\nВсё в силе?"
                    ),
                    keyboard=InlineKeyboard(
                        rows=[
                            [
                                InlineButton(
                                    type="callback",
                                    text="✅ Да, выезжайте",
                                    payload=f"sub:go:{sub_id}:yes",
                                ),
                                InlineButton(
                                    type="callback",
                                    text="⏭ Пропустить",
                                    payload=f"sub:go:{sub_id}:skip",
                                ),
                            ]
                        ]
                    ),
                ),
            )
        return len(targets)
