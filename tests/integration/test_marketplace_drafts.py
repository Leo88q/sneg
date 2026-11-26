"""Черновики — отдельная внутренная модель, не реальные заказы или подписи."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select

from src.infra.db.models import AuditLog, MarketplaceDraftVersion, Order, Payment, SimulatorMessage

URL = "/api/v1/admin/marketplace/drafts"
TERMS = {
    "service_type": "snow_yard",
    "total": "10000.00",
    "schedule": "advance_27",
    "final_payment_route": "direct_to_executor",
}


async def login(http: Any, role: str = "owner") -> None:
    response = await http.post(
        "/api/v1/admin/auth/login",
        json={
            "email": f"{role}@sneg.local",
            "password": "admin12345" if role == "owner" else "foreman12345",
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_drafts_require_admin_and_reject_extra_data(client: Any) -> None:
    http, _ = client
    assert (await http.get(URL)).status_code == 401
    assert (await http.post(URL, json=TERMS)).status_code == 401
    await login(http, "foreman")
    assert (await http.get(URL)).status_code == 403
    assert (await http.post(URL, json=TERMS)).status_code == 403
    await login(http)
    assert (await http.post(URL, json={**TERMS, "client_phone": "79165550000"})).status_code == 422
    assert (await http.post(URL, json={**TERMS, "total": "12.345"})).status_code == 422


@pytest.mark.asyncio
async def test_revision_history_conflicts_archive_and_no_side_effects(client: Any) -> None:
    http, container = client
    await login(http)
    async with container.session_factory() as session:
        before = [
            (await session.execute(select(func.count()).select_from(m))).scalar_one()
            for m in (Order, Payment, SimulatorMessage)
        ]
    created = await http.post(URL, json=TERMS)
    assert created.status_code == 201, created.text
    first = created.json()
    draft_id = first["id"]
    assert first["revision"] == 1
    assert first["versions"][0]["commission"] == "2700.00"
    second = await http.post(
        f"{URL}/{draft_id}/versions", json={**TERMS, "total": "10350.00", "expected_revision": 1}
    )
    assert second.status_code == 201
    assert second.json()["revision"] == 2
    assert second.json()["commission"] == "2794.50"
    stale = await http.post(f"{URL}/{draft_id}/versions", json={**TERMS, "expected_revision": 1})
    assert stale.status_code == 409
    detail = (await http.get(f"{URL}/{draft_id}")).json()
    assert detail["revision"] == 2
    assert [(v["revision"], v["total"]) for v in detail["versions"]] == [
        (1, "10000.00"),
        (2, "10350.00"),
    ]
    assert (
        await http.post(f"{URL}/{draft_id}/archive", json={"expected_revision": 1})
    ).status_code == 409
    assert (
        await http.post(f"{URL}/{draft_id}/archive", json={"expected_revision": 2})
    ).status_code == 200
    assert (
        await http.post(f"{URL}/{draft_id}/versions", json={**TERMS, "expected_revision": 2})
    ).status_code == 409
    assert (await http.get(f"{URL}/{draft_id}")).json()["status"] == "archived"
    assert len((await http.get(URL)).json()) == 1
    async with container.session_factory() as session:
        after = [
            (await session.execute(select(func.count()).select_from(m))).scalar_one()
            for m in (Order, Payment, SimulatorMessage)
        ]
        versions = (
            await session.execute(select(func.count()).select_from(MarketplaceDraftVersion))
        ).scalar_one()
        logs = (
            (await session.execute(select(AuditLog).where(AuditLog.entity_id == draft_id)))
            .scalars()
            .all()
        )
    assert before == after
    assert versions == 2
    assert {log.action for log in logs} == {
        "marketplace_draft.create",
        "marketplace_draft.revise",
        "marketplace_draft.archive",
    }


@pytest.mark.asyncio
async def test_concurrent_edits_only_one_wins(client: Any) -> None:
    http, _ = client
    await login(http)
    draft_id = (await http.post(URL, json=TERMS)).json()["id"]
    results = await asyncio.gather(
        *[
            http.post(
                f"{URL}/{draft_id}/versions", json={**TERMS, "total": total, "expected_revision": 1}
            )
            for total in ("11000", "12000")
        ]
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    result = (await http.get(f"{URL}/{draft_id}")).json()
    assert result["revision"] == 2
    assert [v["revision"] for v in result["versions"]] == [1, 2]
