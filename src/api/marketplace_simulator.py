"""Изолированный демонстрационный сценарий; не создаёт реальные заказы и платежи."""
from __future__ import annotations

import asyncio
import copy
import uuid
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.core.exceptions import InvalidStateTransitionError, ValidationAppError
from src.domain.pricing.marketplace import CENT, MAX_ORDER_TOTAL
from src.domain.pricing.payment_plan import (
    FinalPaymentRoute,
    PaymentSchedule,
    estimate_payment_plan,
)
from src.infra.db.seed import SIM_DISPATCH_CHAT_ID, SIM_PERSONAS
from src.services.messenger.base import MessageTarget, OutgoingMessage
from src.services.messenger.simulator_transport import SimulatorTransport
from src.services.payments.mock import MockPaymentProvider

router = APIRouter(prefix="/api/v1/simulation/marketplace", tags=["local-marketplace-simulation"])


class NewOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: Literal["Уборка снега", "Расчистка дороги", "Помощь по двору"]
    total: Decimal
    expenses: Decimal = Decimal("0")
    schedule: PaymentSchedule
    route: FinalPaymentRoute | None = None
    address: str = Field(min_length=1, max_length=120)
    client_name: str = Field(min_length=1, max_length=80)
    executor_name: str = Field(min_length=1, max_length=80)
    work_date: str = Field(min_length=1, max_length=80)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "take", "client_sign", "executor_sign", "pay_advance", "start", "finish",
        "accept", "pay_balance", "dispute", "rework", "refund", "cancel",
    ]


class Simulation:
    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()


def _state(request: Request) -> Simulation:
    container = request.app.state.container
    cfg = container.settings
    if not (
        cfg.app_env == "development" and cfg.enable_simulator and cfg.is_sqlite
        and cfg.payment_provider == "mock" and cfg.messenger_transport == "simulator"
        and isinstance(container.payment_provider, MockPaymentProvider)
        and isinstance(container.messenger, SimulatorTransport)
    ):
        raise HTTPException(status_code=404, detail="Simulation unavailable")
    return request.app.state.marketplace_simulation  # type: ignore[no-any-return]


def _event(order: dict[str, Any], role: str, text: str) -> None:
    order["events"].append({"role": role, "text": text})


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


async def _deliver_simulated_events(request: Request, events: list[dict[str, str]]) -> None:
    """Bridge to the existing simulated chats, never a real MAX transport."""
    container = request.app.state.container
    if not isinstance(container.messenger, SimulatorTransport):
        return
    for event in events:
        role = event["role"]
        if role == "group":
            target = MessageTarget(chat_id=SIM_DISPATCH_CHAT_ID)
        else:
            persona = SIM_PERSONAS["executor_1" if role == "executor" else "client_1"]
            target = MessageTarget(user_id=persona["max_user_id"])
        await container.messenger.send_message(
            target, OutgoingMessage(text=f"🧪 НОВАЯ МОДЕЛЬ · ТЕСТ\n{event['text']}")
        )


@router.get("")
async def list_orders(request: Request) -> dict[str, Any]:
    state = _state(request)
    async with state.lock:
        return {"orders": copy.deepcopy(list(reversed(list(state.orders.values()))))}


@router.post("")
async def new_order(body: NewOrder, request: Request) -> dict[str, Any]:
    state = _state(request)
    for value in (body.address, body.client_name, body.executor_name, body.work_date):
        if not value.strip():
            raise ValidationAppError("Заполните данные тестового заказа")
    expense = body.expenses
    if not expense.is_finite() or expense < 0 or expense > MAX_ORDER_TOTAL or expense != expense.quantize(CENT):
        raise ValidationAppError("Расходы должны быть неотрицательной суммой с точностью до копеек")
    plan = estimate_payment_plan(body.total, schedule=body.schedule, final_payment_route=body.route)
    order_id = uuid.uuid4().hex[:10]
    order: dict[str, Any] = {
        "id": order_id, "service": body.service, "address": body.address.strip(),
        "client_name": body.client_name.strip(), "executor_name": body.executor_name.strip(),
        "work_date": body.work_date.strip(), "total": _money(plan.estimate.total),
        "commission": _money(plan.estimate.commission),
        "executor_after_commission": _money(plan.estimate.executor_after_commission),
        "expenses": _money(expense),
        "after_expenses": _money(plan.estimate.executor_after_commission - expense),
        "schedule": body.schedule.value, "route": body.route.value if body.route else None,
        "advance": _money(plan.upfront), "balance": _money(plan.after_completion),
        "paid": "0.00", "refunded": "0.00", "status": "searching",
        "client_signed": False, "executor_signed": False, "events": [],
    }
    _event(order, "client", f"Создана тестовая заявка: {body.service}, {order['total']} ₽.")
    schedule_label = {
        PaymentSchedule.ADVANCE_27: "аванс 27%, остаток после работ",
        PaymentSchedule.FULL_UPFRONT: "полная предоплата",
        PaymentSchedule.AFTER_COMPLETION: "оплата после работ",
    }[body.schedule]
    route_label = {
        FinalPaymentRoute.PAYMENT_PROVIDER: "через партнёра",
        FinalPaymentRoute.DIRECT_TO_EXECUTOR: "напрямую исполнителю",
        None: "остатка нет",
    }[body.route]
    _event(order, "group", f"Новая заявка #{order_id}: {body.service}, цена {order['total']} ₽; комиссия 27% — {order['commission']} ₽, после комиссии {order['executor_after_commission']} ₽, расходы {order['expenses']} ₽, остаток после расходов {order['after_expenses']} ₽. График: {schedule_label}, оплата остатка: {route_label} (тест).")
    async with state.lock:
        state.orders[order_id] = order
        result = copy.deepcopy(order)
    await _deliver_simulated_events(request, result["events"])
    return result


@router.post("/{order_id}/action")
async def act(order_id: str, body: Action, request: Request) -> dict[str, Any]:
    state = _state(request)
    async with state.lock:
        order = state.orders.get(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Тестовый заказ не найден")
        action, status = body.action, order["status"]
        previous_events = len(order["events"])
        if action == "take" and status == "searching":
            if Decimal(order["after_expenses"]) <= 0:
                raise ValidationAppError("После комиссии и расходов нет вознаграждения: измените условия и создайте новый тестовый заказ")
            order["status"] = "agreement"
            _event(order, "executor", f"{order['executor_name']} принял тестовые условия. Сторонам показан макет договора.")
        elif action in ("client_sign", "executor_sign") and status == "agreement":
            key = "client_signed" if action == "client_sign" else "executor_signed"
            if order[key]:
                raise InvalidStateTransitionError("Подтверждение уже выполнено")
            order[key] = True
            _event(order, "client" if action == "client_sign" else "executor", "Тестовое подтверждение условий; это не электронная подпись.")
            if order["client_signed"] and order["executor_signed"]:
                order["status"] = "awaiting_advance" if Decimal(order["advance"]) > 0 else "ready"
                _event(order, "client", "Обе стороны подтвердили макет. Далее — тестовая оплата или начало работ.")
        elif action == "pay_advance" and status == "awaiting_advance":
            order["paid"] = order["advance"]
            order["status"] = "ready"
            _event(order, "client", f"Имитация оплаты {order['advance']} ₽. Денежного перевода нет.")
        elif action == "start" and status == "ready":
            order["status"] = "in_progress"
            _event(order, "executor", "Исполнитель начал работу (тест).")
        elif action == "finish" and status == "in_progress":
            order["status"] = "awaiting_acceptance"
            _event(order, "executor", "Исполнитель сообщил о завершении работы (тест).")
        elif action == "accept" and status == "awaiting_acceptance":
            order["status"] = "awaiting_balance" if Decimal(order["balance"]) > 0 else "completed"
            _event(order, "client", "Клиент принял работу (тест).")
        elif action == "pay_balance" and status == "awaiting_balance":
            order["paid"] = order["total"]
            order["status"] = "completed"
            route = "напрямую исполнителю" if order["route"] == "direct_to_executor" else "через партнёра"
            _event(order, "client", f"Имитация оплаты остатка {order['balance']} ₽ {route}. Денежного перевода нет.")
        elif action == "dispute" and status == "awaiting_acceptance":
            order["status"] = "disputed"
            _event(order, "client", "Открыто тестовое обращение о качестве работы.")
        elif action == "rework" and status == "disputed":
            order["status"] = "in_progress"
            _event(order, "support", "Стороны согласовали тестовую доработку.")
        elif action == "refund" and status == "disputed":
            order["refunded"] = order["paid"]
            order["status"] = "canceled"
            _event(order, "support", f"Имитация возврата {order['refunded']} ₽ и закрытие обращения. Деньги не переводились.")
        elif action == "cancel" and status in ("searching", "agreement", "awaiting_advance", "ready", "in_progress"):
            order["refunded"] = order["paid"]
            order["status"] = "canceled"
            _event(order, "support", f"Тестовая отмена, имитация возврата {order['refunded']} ₽. Реальный возврат требует отдельной проверки.")
        else:
            raise InvalidStateTransitionError("Действие недоступно на текущем этапе")
        result = copy.deepcopy(order)
    await _deliver_simulated_events(request, result["events"][previous_events:])
    return result
