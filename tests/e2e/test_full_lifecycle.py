from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from src.core.container import AppContainer
from src.core.enums import OrderStatus, ServiceType
from src.infra.db.models import Dispute, Order, TariffConfig


@pytest.mark.asyncio
async def test_e2e_snow_yard_full_lifecycle_and_payout(
    client: tuple[httpx.AsyncClient, AppContainer],
) -> None:
    ac, container = client

    # 1. Client starts bot and accepts consents
    await ac.post("/api/v1/simulator/bot-start", json={"persona": "client_1"})
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "auth:accept_consents"},
    )

    # 2. Client chooses snow_yard -> Malakhovka geo -> address -> 120m² -> 10-30cm -> standard -> within_3h -> no removal -> bot_proxy -> confirm
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "svc:snow_yard"},
    )
    await ac.post(
        "/api/v1/simulator/send-message",
        json={"persona": "client_1", "location": {"latitude": 55.6435, "longitude": 38.0068}},
    )
    await ac.post(
        "/api/v1/simulator/send-message",
        json={"persona": "client_1", "text": "пос. Малаховка, ул. Садовая, д. 22"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "ord:area:120"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "ord:depth:from_10_to_30"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "ord:access:standard"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "ord:urg:within_3h"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "ord:rem:no"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "ord:contact:bot_proxy"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "ord:confirm"},
    )

    async with container.session_factory() as session:
        order = (await session.execute(select(Order))).scalar_one()
        assert order.service_type == ServiceType.SNOW_YARD.value
        assert order.estimated_total >= Decimal("5200.00")
        assert order.advance_amount >= Decimal("1550.00")
        assert order.status == OrderStatus.AWAITING_ADVANCE.value
        order_id = str(order.id)
        expected_final_total = order.final_total
        expected_advance = order.advance_amount

    # 3. Client pays advance via Mock SBP trigger -> order published to dispatch chat WITHOUT exact address
    r_pay1 = await ac.post("/api/v1/simulator/trigger/pay-latest", json={"result": "succeeded"})
    assert r_pay1.status_code == 200
    assert r_pay1.json()["purpose"] == "advance"

    sim_state = (await ac.get("/api/v1/simulator/state")).json()
    dispatch_msgs = sim_state["messages_by_chat"][str(sim_state["dispatch_chat_id"])]
    assert len(dispatch_msgs) >= 1
    assert "Садовая" not in dispatch_msgs[-1]["text"]  # Address privacy!
    assert "22" not in dispatch_msgs[-1]["text"]

    # 4. Executor 1 clicks «Беру» -> gets exact address in DM
    await ac.post(
        "/api/v1/simulator/callback",
        json={
            "persona": "executor_1",
            "chat_id": sim_state["dispatch_chat_id"],
            "payload": f"ord:take:{order_id}",
        },
    )
    sim_state = (await ac.get("/api/v1/simulator/state")).json()
    exec1_msgs = sim_state["messages_by_chat"]["20001"]
    assert any("Садовая, д. 22" in m["text"] for m in exec1_msgs)

    # 5. Executor lifecycle: en_route -> arrived -> blocked without BEFORE photo -> BEFORE photo -> in_progress -> AFTER photo -> awaiting_acceptance
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "executor_1", "chat_id": 20001, "payload": f"ord:st:{order_id}:en_route"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "executor_1", "chat_id": 20001, "payload": f"ord:st:{order_id}:arrived"},
    )
    # Trying to start without BEFORE photo must remain ARRIVED
    await ac.post(
        "/api/v1/simulator/callback",
        json={
            "persona": "executor_1",
            "chat_id": 20001,
            "payload": f"ord:st:{order_id}:in_progress",
        },
    )
    async with container.session_factory() as session:
        o_check = (await session.execute(select(Order))).scalar_one()
        assert o_check.status == OrderStatus.ARRIVED.value

    # Upload BEFORE photo -> start work
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "executor_1", "chat_id": 20001, "payload": f"ord:photo:{order_id}:before"},
    )
    await ac.post(
        "/api/v1/simulator/upload-photo",
        data={"persona": "executor_1", "preset": "before"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={
            "persona": "executor_1",
            "chat_id": 20001,
            "payload": f"ord:st:{order_id}:in_progress",
        },
    )
    # Upload AFTER photo -> finish work
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "executor_1", "chat_id": 20001, "payload": f"ord:photo:{order_id}:after"},
    )
    await ac.post(
        "/api/v1/simulator/upload-photo",
        data={"persona": "executor_1", "preset": "after"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={
            "persona": "executor_1",
            "chat_id": 20001,
            "payload": f"ord:st:{order_id}:awaiting_acceptance",
        },
    )

    # 6. Client accepts work -> pays remainder -> rates 5★
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": f"acc:ok:{order_id}"},
    )
    r_pay2 = await ac.post("/api/v1/simulator/trigger/pay-latest", json={"result": "succeeded"})
    assert r_pay2.status_code == 200
    assert r_pay2.json()["purpose"] == "final"
    assert Decimal(r_pay2.json()["amount"]) == expected_final_total - expected_advance

    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": f"ord:rate:{order_id}:5"},
    )

    async with container.session_factory() as session:
        o_done = (await session.execute(select(Order))).scalar_one()
        assert o_done.status == OrderStatus.COMPLETED.value
        assert o_done.paid_amount == expected_final_total
        assert o_done.client_rating == 5

    # 7. Admin logs in, verifies executor balance, creates payout batch, marks paid with НПД receipt & exports CSV
    login_resp = await ac.post(
        "/api/v1/admin/auth/login",
        json={"email": "owner@sneg.local", "password": "admin12345"},
    )
    assert login_resp.status_code == 200

    balances = (await ac.get("/api/v1/admin/payouts/balances")).json()
    ivan = next(b for b in balances if "Иван" in b["display_name"])
    assert Decimal(ivan["accrued_rub"]) > Decimal("3000.00")

    batch = (
        await ac.post("/api/v1/admin/payouts/batches", json={"executor_id": ivan["executor_id"]})
    ).json()
    assert Decimal(batch["total_amount"]) == Decimal(ivan["accrued_rub"])

    paid = (
        await ac.post(
            f"/api/v1/admin/payouts/batches/{batch['id']}/mark-paid",
            json={
                "payment_method_note": "СБП",
                "npd_receipt_id": "NPD-2026-999",
                "npd_receipt_url": "https://lknpd.nalog.ru/r/999",
            },
        )
    ).json()
    assert paid["status"] == "paid"

    csv_resp = await ac.get("/api/v1/admin/payouts/batches/export.csv")
    assert csv_resp.status_code == 200
    assert "NPD-2026-999" in csv_resp.text


@pytest.mark.asyncio
async def test_e2e_car_rescue_4_step_flow(
    client: tuple[httpx.AsyncClient, AppContainer],
) -> None:
    ac, container = client
    await ac.post("/api/v1/simulator/bot-start", json={"persona": "client_2"})
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_2", "chat_id": 10002, "payload": "auth:accept_consents"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_2", "chat_id": 10002, "payload": "svc:car_rescue"},
    )
    # Step 1: geo
    await ac.post(
        "/api/v1/simulator/send-message",
        json={"persona": "client_2", "location": {"latitude": 55.6435, "longitude": 38.0068}},
    )
    # Step 2: photo + comment
    await ac.post(
        "/api/v1/simulator/upload-photo",
        data={"persona": "client_2", "preset": "car_stuck", "caption": "Застрял у СНТ"},
    )
    # Step 3: vehicle type
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_2", "chat_id": 10002, "payload": "ord:veh:suv"},
    )
    # Step 4: contact mode + confirm
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_2", "chat_id": 10002, "payload": "ord:contact:bot_proxy"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_2", "chat_id": 10002, "payload": "ord:confirm"},
    )

    async with container.session_factory() as session:
        order = (await session.execute(select(Order))).scalar_one()
        assert order.service_type == ServiceType.CAR_RESCUE.value
        assert order.estimated_total >= Decimal("3500.00")
        assert order.advance_amount == (order.estimated_total * Decimal("0.5")).quantize(
            Decimal("0.01")
        ) or order.advance_amount >= Decimal("1750.00")


@pytest.mark.asyncio
async def test_admin_rbac_price_adjustment_dispute_and_tariff_immutability(
    client: tuple[httpx.AsyncClient, AppContainer],
) -> None:
    ac, container = client

    # 1. Foreman login -> allowed to read tariffs, FORBIDDEN from modifying tariffs!
    r_f = await ac.post(
        "/api/v1/admin/auth/login",
        json={"email": "foreman@sneg.local", "password": "foreman12345"},
    )
    assert r_f.status_code == 200
    cur_tariff = (await ac.get("/api/v1/admin/tariffs/current")).json()
    r_forbid = await ac.put(
        "/api/v1/admin/tariffs",
        json={"config_json": cur_tariff["config_json"], "comment": "hack"},
    )
    assert r_forbid.status_code == 403

    # 2. Owner login -> allowed to publish Tariff v2; historical order keeps v1 snapshot
    await ac.post(
        "/api/v1/admin/auth/login",
        json={"email": "owner@sneg.local", "password": "admin12345"},
    )

    # Create a yard_help order under v1
    await ac.post("/api/v1/simulator/bot-start", json={"persona": "client_1"})
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "svc:yard_help"},
    )
    await ac.post(
        "/api/v1/simulator/send-message",
        json={"persona": "client_1", "location": {"latitude": 55.6435, "longitude": 38.0068}},
    )
    await ac.post(
        "/api/v1/simulator/send-message",
        json={"persona": "client_1", "text": "Малаховка, ул. Лесная 5"},
    )
    await ac.post(
        "/api/v1/simulator/send-message",
        json={"persona": "client_1", "text": "1 2"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "ord:contact:bot_proxy"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": "ord:confirm"},
    )
    await ac.post("/api/v1/simulator/trigger/pay-latest", json={"result": "succeeded"})

    async with container.session_factory() as session:
        order = (await session.execute(select(Order))).scalar_one()
        orig_total = order.final_total
        order_id = str(order.id)

    # Owner publishes Tariff v2
    new_cfg = dict(cur_tariff["config_json"])
    new_cfg["rounding_step_rub"] = 100
    r_v2 = await ac.put(
        "/api/v1/admin/tariffs",
        json={"config_json": new_cfg, "comment": "Winter surge v2"},
    )
    assert r_v2.status_code == 200
    assert r_v2.json()["version"] == 2

    # Historical order stays untouched
    async with container.session_factory() as session:
        o_after_v2 = (await session.execute(select(Order))).scalar_one()
        assert o_after_v2.final_total == orig_total
        tariffs = (await session.execute(select(TariffConfig))).scalars().all()
        assert len(tariffs) == 2

    # 3. Assign executor -> en_route -> arrived -> propose >20% price adjustment -> client approves
    execs = (await ac.get("/api/v1/admin/executors")).json()
    ivan_id = next(e["user_id"] for e in execs if "Иван" in e["display_name"])
    await ac.post(f"/api/v1/admin/orders/{order_id}/assign", json={"executor_user_id": ivan_id})
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "executor_1", "chat_id": 20001, "payload": f"ord:st:{order_id}:en_route"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "executor_1", "chat_id": 20001, "payload": f"ord:st:{order_id}:arrived"},
    )

    adj_resp = await ac.post(
        f"/api/v1/admin/orders/{order_id}/price-adjustment",
        json={"new_total": "4500", "reason": "Дополнительный объём на участке"},
    )
    assert adj_resp.status_code == 200
    adj_id = adj_resp.json()["id"]

    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": f"adj:ok:{adj_id}"},
    )

    async with container.session_factory() as session:
        o_adj = (await session.execute(select(Order))).scalar_one()
        assert o_adj.final_total == Decimal("4500.00")

    # 4. Upload BEFORE + AFTER photos -> finish -> client opens dispute -> owner resolves with discount
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "executor_1", "chat_id": 20001, "payload": f"ord:photo:{order_id}:before"},
    )
    await ac.post(
        "/api/v1/simulator/upload-photo",
        data={"persona": "executor_1", "preset": "before"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={
            "persona": "executor_1",
            "chat_id": 20001,
            "payload": f"ord:st:{order_id}:in_progress",
        },
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "executor_1", "chat_id": 20001, "payload": f"ord:photo:{order_id}:after"},
    )
    await ac.post(
        "/api/v1/simulator/upload-photo",
        data={"persona": "executor_1", "preset": "after"},
    )
    await ac.post(
        "/api/v1/simulator/callback",
        json={
            "persona": "executor_1",
            "chat_id": 20001,
            "payload": f"ord:st:{order_id}:awaiting_acceptance",
        },
    )

    # Client opens dispute
    await ac.post(
        "/api/v1/simulator/callback",
        json={"persona": "client_1", "chat_id": 10001, "payload": f"acc:disp:{order_id}"},
    )
    await ac.post(
        "/api/v1/simulator/send-message",
        json={"persona": "client_1", "text": "Остался снег у калитки"},
    )

    disputes = (await ac.get("/api/v1/admin/disputes")).json()
    assert len(disputes) == 1
    d_id = disputes[0]["id"]

    r_res = await ac.post(
        f"/api/v1/admin/disputes/{d_id}/resolve",
        json={
            "resolution": "resolved_discount",
            "note": "Скидка 500 ₽ за нерасчищенную калитку",
            "discount_amount": "500",
        },
    )
    assert r_res.status_code == 200

    async with container.session_factory() as session:
        o_disc = (await session.execute(select(Order))).scalar_one()
        assert o_disc.final_total == Decimal("4000.00")
        assert o_disc.status == OrderStatus.AWAITING_FINAL_PAYMENT.value
        d_db = (await session.execute(select(Dispute))).scalar_one()
        assert d_db.status == "resolved_discount"
