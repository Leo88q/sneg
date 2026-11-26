from typing import Any

import httpx
import pytest

from src.core.config import Settings
from src.main import create_app

BASE = "/api/v1/simulation/marketplace"


def payload(schedule: str = "advance_27") -> dict[str, Any]:
    return {
        "service": "Расчистка дороги", "total": "10000.00", "expenses": "2000.00",
        "schedule": schedule, "route": None if schedule == "full_upfront" else "direct_to_executor",
        "client_name": "Тестовый клиент", "executor_name": "Тестовый исполнитель",
        "address": "Тестовая улица", "work_date": "Завтра",
    }


async def act(http: Any, order_id: str, action: str) -> Any:
    return await http.post(f"{BASE}/{order_id}/action", json={"action": action})


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule,advance,balance", [
    ("advance_27", "2700.00", "7300.00"),
    ("full_upfront", "10000.00", "0.00"),
    ("after_completion", "0.00", "10000.00"),
])
async def test_complete_simulated_order(client: Any, schedule: str, advance: str, balance: str) -> None:
    http, _ = client
    assert (await http.get("/simulator/marketplace")).status_code == 200
    result = await http.post(BASE, json=payload(schedule))
    assert result.status_code == 200
    order = result.json()
    assert (order["commission"], order["executor_after_commission"], order["after_expenses"]) == (
        "2700.00", "7300.00", "5300.00"
    )
    assert (order["advance"], order["balance"]) == (advance, balance)
    oid = order["id"]
    assert (await act(http, oid, "start")).status_code == 409
    for action in ("take", "client_sign", "executor_sign"):
        assert (await act(http, oid, action)).status_code == 200
    if advance != "0.00":
        assert (await act(http, oid, "pay_advance")).status_code == 200
        assert (await act(http, oid, "pay_advance")).status_code == 409
    for action in ("start", "finish", "accept"):
        assert (await act(http, oid, action)).status_code == 200
    if balance != "0.00":
        assert (await act(http, oid, "pay_balance")).status_code == 200
    result = (await http.get(BASE)).json()["orders"][0]
    assert result["status"] == "completed" and result["paid"] == "10000.00"
    assert result["refunded"] == "0.00"
    # This is an isolated simulator: no actual orders or payments were created.
    assert (await http.get("/api/v1/simulator/state")).json()["recent_orders"] == []


@pytest.mark.asyncio
async def test_cancellation_dispute_and_nonviable_economics(client: Any) -> None:
    http, _ = client
    data = payload()
    data["expenses"] = "7300.00"
    order = (await http.post(BASE, json=data)).json()
    assert (await act(http, order["id"], "take")).status_code == 422
    assert (await act(http, order["id"], "cancel")).status_code == 200

    order = (await http.post(BASE, json=payload())).json()
    oid = order["id"]
    for action in ("take", "client_sign", "executor_sign", "pay_advance", "start", "finish", "dispute"):
        assert (await act(http, oid, action)).status_code == 200
    r = await act(http, oid, "rework")
    assert r.json()["status"] == "in_progress"
    for action in ("finish", "dispute", "refund"):
        r = await act(http, oid, action)
        assert r.status_code == 200
    assert r.json()["status"] == "canceled" and r.json()["refunded"] == "2700.00"
    assert (await act(http, oid, "refund")).status_code == 409


@pytest.mark.asyncio
async def test_invalid_inputs(client: Any) -> None:
    http, _ = client
    for patch in ({"expenses": "-1"}, {"expenses": "0.001"}, {"total": "0"}, {"route": None}):
        data = payload()
        data.update(patch)
        assert (await http.post(BASE, json=data)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"app_env": "staging"}, {"enable_simulator": False},
    {"messenger_transport": "max"}, {"payment_provider": "yookassa"},
    {"database_url": "postgresql+asyncpg://user:password@localhost/test"},
])
async def test_simulation_not_available_outside_safe_settings(test_settings: Settings, changes: Any) -> None:
    app = create_app(settings=test_settings.model_copy(update=changes))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get(BASE)).status_code == 404
        assert (await http.post(BASE, json=payload())).status_code == 404
        assert (await http.get("/simulator/marketplace")).status_code == 404
    await app.state.container.shutdown()


@pytest.mark.asyncio
async def test_simulated_messages_reach_existing_max_simulator(client: Any) -> None:
    http, _ = client
    order = (await http.post(BASE, json=payload())).json()
    assert (await act(http, order["id"], "take")).status_code == 200
    state = (await http.get("/api/v1/simulator/state")).json()
    messages = state["messages_by_chat"]
    assert any("комиссия 27%" in m["text"] for m in messages[str(state["dispatch_chat_id"])])
    assert any("принял тестовые условия" in m["text"] for m in messages["20001"])
    assert any("Создана тестовая заявка" in m["text"] for m in messages["10001"])
