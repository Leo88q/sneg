"""Предпросмотр новой модели — только админский расчёт без побочных эффектов."""

from typing import Any

import pytest
from sqlalchemy import func, select

from src.infra.db.models import Order, Payment, SimulatorMessage


@pytest.mark.asyncio
async def test_preview_requires_owner_or_admin(client: Any) -> None:
    http, _ = client
    payload = {"total": "10000", "schedule": "advance_27", "final_payment_route": "payment_provider"}
    assert (await http.post("/api/v1/admin/marketplace/preview", json=payload)).status_code == 401
    login = await http.post(
        "/api/v1/admin/auth/login",
        json={"email": "foreman@sneg.local", "password": "foreman12345"},
    )
    assert login.status_code == 200
    assert (await http.post("/api/v1/admin/marketplace/preview", json=payload)).status_code == 403


@pytest.mark.asyncio
async def test_preview_for_each_payment_route_and_schedule_has_no_side_effects(client: Any) -> None:
    http, container = client
    login = await http.post(
        "/api/v1/admin/auth/login", json={"email": "owner@sneg.local", "password": "admin12345"}
    )
    assert login.status_code == 200
    async with container.session_factory() as session:
        before = [
            (await session.execute(select(func.count()).select_from(model))).scalar_one()
            for model in (Order, Payment, SimulatorMessage)
        ]
    for schedule, route, upfront, after in [
        ("advance_27", "payment_provider", "2700.00", "7300.00"),
        ("advance_27", "direct_to_executor", "2700.00", "7300.00"),
        ("full_upfront", None, "10000.00", "0.00"),
        ("after_completion", "direct_to_executor", "0.00", "10000.00"),
        ("after_completion", "payment_provider", "0.00", "10000.00"),
    ]:
        resp = await http.post(
            "/api/v1/admin/marketplace/preview",
            json={"total": "10000.00", "schedule": schedule, "final_payment_route": route},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["preview_only"] is True
        assert data["commission"] == "2700.00"
        assert data["executor_after_commission"] == "7300.00"
        assert data["upfront"] == upfront
        assert data["after_completion"] == after
        assert "ПРОЕКТ" in data["site_example"]
        assert "ПРОЕКТ" in data["bot_example"]
        assert "ПРОЕКТ" in data["group_example"]
        assert "до налогов и расходов" in data["group_example"]
    async with container.session_factory() as session:
        after_counts = [
            (await session.execute(select(func.count()).select_from(model))).scalar_one()
            for model in (Order, Payment, SimulatorMessage)
        ]
    assert before == after_counts


@pytest.mark.asyncio
async def test_preview_rejects_invalid_money_and_route(client: Any) -> None:
    http, _ = client
    await http.post(
        "/api/v1/admin/auth/login", json={"email": "owner@sneg.local", "password": "admin12345"}
    )
    for payload in [
        {"total": "1.001", "schedule": "advance_27", "final_payment_route": "payment_provider"},
        {"total": "0", "schedule": "full_upfront"},
        {"total": "100", "schedule": "advance_27"},
        {"total": "100", "schedule": "full_upfront", "final_payment_route": "payment_provider"},
        {"total": "100", "schedule": "unknown"},
    ]:
        assert (await http.post("/api/v1/admin/marketplace/preview", json=payload)).status_code == 422
