"""Интеграционные тесты витрины, живого калькулятора и онлайн-заказа с сайта."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from src.infra.db.models import DataSubjectRequest, Payment, User, UserConsent


@pytest.mark.asyncio
async def test_homepage_is_customer_order_storefront_not_legal_or_dev_panel(client: Any) -> None:
    """Главная страница / — витрина с калькулятором и формой заказа без dev-телеметрии."""
    http, _container = client
    resp = await http.get("/")
    assert resp.status_code == 200
    body = resp.text

    # Главный заказной функционал присутствует
    assert 'id="order"' in body
    assert 'id="public-order-form"' in body
    assert "/static/storefront.js" in body
    assert "+7 (977) 924-02-93" in body

    # Внутренней dev-телеметрии нет на главной
    assert "СТАТУС ПЛАТФОРМЫ" not in body
    assert "ТЕСТОВЫЕ УЧЁТНЫЕ ЗАПИСИ" not in body

    # Все юридические реквизиты и ссылки аккуратно сохранены в футере
    assert "ИП Холматов Леонид Икрамович" in body
    assert "231521684300" in body
    assert "/legal/privacy" in body
    assert "/legal/offer" in body
    assert "/legal/my-data" in body


@pytest.mark.asyncio
async def test_public_catalog_and_live_quote_across_services(client: Any) -> None:
    """Каталог и живой калькулятор возвращают реальные зоны и точные тарифы."""
    http, _container = client
    cat_resp = await http.get("/api/v1/public/catalog")
    assert cat_resp.status_code == 200
    cat = cat_resp.json()

    assert cat["brand_name"] == "ЧистоДвор"
    assert cat["min_order_total_rub"] == 2000
    assert cat["phone"] == "+7 (977) 924-02-93"
    assert len(cat["services"]) == 7
    svc_by_code = {s["code"]: s for s in cat["services"]}
    assert svc_by_code["snow_yard"]["price_from_label"] == "от 40 ₽/м²"
    assert svc_by_code["snow_road"]["price_from_label"] == "от 30 ₽/м²"
    assert svc_by_code["car_rescue"]["price_from_label"] == "от 3500 ₽"
    assert svc_by_code["movers"]["price_from_label"] == "от 750 ₽/ч"
    assert svc_by_code["reagents"]["price_from_label"] == "от 20 ₽/м²"
    zone_codes = {z["code"] for z in cat["zones"]}
    assert {"malakhovka", "tomilino", "kraskovo", "bykovo", "lytkarino"}.issubset(zone_codes)

    # Проверяем расчёт по нескольким услугам
    samples = [
        {
            "service_type": "snow_yard",
            "zone_code": "malakhovka",
            "area_m2": 100,
            "depth_bucket": "from_10_to_30",
            "access_constraint": "standard",
            "urgency": "today",
            "snow_removal": False,
        },
        {
            "service_type": "snow_road",
            "zone_code": "kraskovo",
            "length_m": 80,
            "width_m": 4,
            "depth_bucket": "over_30",
            "access_constraint": "wide_road",
            "urgency": "today",
            "snow_removal": True,
        },
        {
            "service_type": "car_rescue",
            "zone_code": "tomilino",
            "vehicle_type": "suv",
            "distance_km": 8,
            "is_urgent_rescue": True,
        },
        {
            "service_type": "movers",
            "zone_code": "bykovo",
            "workers_count": 2,
            "hours": 3,
            "floors_without_lift": 2,
        },
        {
            "service_type": "roof_cleaning",
            "zone_code": "lytkarino",
            "area_m2": 100,
        },
        {
            "service_type": "reagents",
            "zone_code": "malakhovka",
            "area_m2": 120,
            "as_addon": True,
        },
    ]
    for payload in samples:
        q_resp = await http.post("/api/v1/public/orders/quote", json=payload)
        assert q_resp.status_code == 200, f"Quote failed for {payload}: {q_resp.text}"
        q = q_resp.json()
        total = float(q["estimated_total"])
        advance = float(q["advance_amount"])
        remainder = float(q["remainder_amount"])
        assert total > 0
        assert total % 50 == 0
        assert abs((advance + remainder) - total) < 0.01
        if payload.get("snow_removal"):
            assert q["advance_pct"] == 50


@pytest.mark.asyncio
async def test_public_order_creation_consents_status_admin_and_lifecycle(client: Any) -> None:
    """Полный цикл веб-заказа: проверки 152-ФЗ -> создание -> админка -> оплата -> DSR по телефону."""
    http, container = client

    base_order = {
        "service_type": "snow_yard",
        "zone_code": "malakhovka",
        "area_m2": 120,
        "depth_bucket": "from_10_to_30",
        "access_constraint": "standard",
        "urgency": "today",
        "snow_removal": False,
        "address_text": "ул. Красковская, д. 18",
        "client_name": "Ольга Морозова",
        "client_phone": "8 (916) 555-43-21",
        "client_email": "olga@example.ru",
        "client_comment": "Калитка слева, код 1234",
    }

    # 1. Без обязательных согласий — 400
    r_no_pdn = await http.post(
        "/api/v1/public/orders",
        json={**base_order, "consent_pdn": False, "consent_offer": True},
    )
    assert r_no_pdn.status_code == 400

    r_no_offer = await http.post(
        "/api/v1/public/orders",
        json={**base_order, "consent_pdn": True, "consent_offer": False},
    )
    assert r_no_offer.status_code == 400

    # 2. Некорректный телефон — 422
    r_bad_phone = await http.post(
        "/api/v1/public/orders",
        json={
            **base_order,
            "client_phone": "12345",
            "consent_pdn": True,
            "consent_offer": True,
        },
    )
    assert r_bad_phone.status_code == 422

    # 3. Успешное оформление заказа с сайта
    create_resp = await http.post(
        "/api/v1/public/orders",
        json={**base_order, "consent_pdn": True, "consent_offer": True},
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    pub_num = created["public_number"]
    order_id = created["order_id"]
    assert created["phone_last4"] == "4321"
    assert created["status"] == "awaiting_advance"
    assert created["zone_name"] == "Малаховка"
    assert float(created["estimated_total"]) > 0

    # 4. Проверяем, что согласия и веб-пользователь сохранились в БД
    async with container.session_factory() as session:
        web_user = (
            await session.execute(
                select(User).where(User.shared_contact_phone == "+79165554321")
            )
        ).scalar_one()
        assert web_user.max_user_id < 0
        consents = list(
            (
                await session.execute(
                    select(UserConsent).where(
                        UserConsent.user_id == web_user.id,
                        UserConsent.source == "web_form",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {c.consent_type for c in consents} == {"pdn", "client_offer", "phone_transfer"}

    # 5. Проверка статуса по номеру заказа + последние 4 цифры телефона
    bad_status = await http.get(f"/api/v1/public/orders/{pub_num}/status?phone_last4=0000")
    assert bad_status.status_code == 404

    ok_status = await http.get(f"/api/v1/public/orders/{pub_num}/status?phone_last4=4321")
    assert ok_status.status_code == 200
    assert ok_status.json()["status"] == "awaiting_advance"

    # 6. Заказ виден в веб-админке с телефоном, комментарием и источником web_site
    login = await http.post(
        "/api/v1/admin/auth/login",
        json={"email": "owner@sneg.local", "password": "admin12345"},
    )
    assert login.status_code == 200

    admin_orders = (await http.get("/api/v1/admin/orders")).json()
    found = next(o for o in admin_orders if o["id"] == order_id)
    assert found["source"] == "web_site"
    assert found["client_phone"] == "+79165554321"

    detail = (await http.get(f"/api/v1/admin/orders/{order_id}")).json()
    assert detail["source"] == "web_site"
    assert detail["client_comment"] == "Калитка слева, код 1234"
    assert detail["client"]["email"] == "olga@example.ru"
    assert detail["client"]["shared_contact_phone"] == "+79165554321"

    # 7. Подтверждение аванса для веб-клиента не падает на отрицательном max_user_id
    async with container.session_factory() as session:
        pay = (
            await session.execute(
                select(Payment).where(Payment.order_id == uuid.UUID(order_id))
            )
        ).scalar_one()
        pay_id = pay.id

    _p, updated_order = await container.orchestrator.confirm_payment_succeeded(pay_id)
    assert updated_order is not None
    assert updated_order.status == "searching_executor"

    # Статус на сайте обновился
    ok_status2 = await http.get(f"/api/v1/public/orders/{pub_num}/status?phone_last4=4321")
    assert ok_status2.status_code == 200
    assert ok_status2.json()["status"] == "searching_executor"

    # 8. Рассылка из админки пропускает веб-клиентов (max_user_id <= 0)
    bc = await http.post(
        "/api/v1/admin/broadcasts",
        json={"target_role": "client", "text": "Тестовая рассылка"},
    )
    assert bc.status_code == 200

    # 9. DSR-запрос по номеру телефона связывается с веб-клиентом
    dsr_resp = await http.post(
        "/api/v1/legal/data-requests",
        json={
            "request_type": "export_data",
            "contact_value": "+7 (916) 555-43-21",
            "consent_accepted": True,
            "age_confirmed": True,
        },
    )
    assert dsr_resp.status_code == 201
    ticket = dsr_resp.json()["ticket_code"]
    async with container.session_factory() as session:
        dsr = (
            await session.execute(
                select(DataSubjectRequest).where(DataSubjectRequest.ticket_code == ticket)
            )
        ).scalar_one()
        assert dsr.user_id is not None


@pytest.mark.asyncio
async def test_staging_mode_hides_mock_payment_url_on_public_orders(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В staging (при mock-платежах) сайт не отдаёт ссылку на /mock-pay, а включает подтверждение диспетчером."""
    http, container = client
    monkeypatch.setattr(container.settings, "app_env", "staging")
    monkeypatch.setattr(container.settings, "payment_provider", "mock")

    resp = await http.post(
        "/api/v1/public/orders",
        json={
            "service_type": "car_rescue",
            "zone_code": "malakhovka",
            "vehicle_type": "sedan",
            "distance_km": 4,
            "is_urgent_rescue": True,
            "address_text": "Малаховка, ул. Февральская, 7",
            "client_name": "Игорь",
            "client_phone": "+7 926 111-22-33",
            "consent_pdn": True,
            "consent_offer": True,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["payment_mode"] == "dispatcher_confirm"
    assert data["payment_url"] is None


@pytest.mark.asyncio
async def test_quick_order_without_client_name_succeeds(client: Any) -> None:
    """Упрощённая 3-шаговая форма позволяет заказать только по адресу и телефону (имя опционально)."""
    http, container = client
    resp = await http.post(
        "/api/v1/public/orders",
        json={
            "service_type": "snow_yard",
            "zone_code": "malakhovka",
            "area_m2": 100,
            "address_text": "ул. Быковская, д. 42",
            "client_phone": "+7 (903) 777-88-99",
            "consent_pdn": True,
            "consent_offer": True,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["phone_last4"] == "8899"
    assert data["zone_name"] == "Малаховка"

    async with container.session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.shared_contact_phone == "+79037778899")
            )
        ).scalar_one()
        assert user.first_name == "Клиент"


@pytest.mark.asyncio
async def test_owner_alerts_and_manual_dispatch_to_workers(client: Any) -> None:
    """Полный цикл маршрутизации: сайт -> MAX уведомление владельцу -> отправка в чат рабочих."""
    from src.core.enums import ChatPurpose
    from src.infra.db.models import BotChat, Order, SimulatorMessage

    http, container = client

    owner_max_uid = 777001
    owner_chat_id = 777001
    brigade_chat_id = -999888

    # 1. Неверный пароль не привязывает чат уведомлений
    await container.bot_router.handle_update(
        {
            "update_id": "bind-bad-1",
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": owner_max_uid, "first_name": "Леонид"},
                "recipient": {"chat_id": owner_chat_id, "chat_type": "dialog"},
                "body": {"text": "/bind_alerts_chat wrongpass"},
            },
        }
    )
    async with container.session_factory() as session:
        assert await session.get(BotChat, owner_chat_id) is None

    # 2. Верный пароль владельца привязывает личный чат к уведомлениям и даёт роль owner
    await container.bot_router.handle_update(
        {
            "update_id": "bind-ok-1",
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": owner_max_uid, "first_name": "Леонид"},
                "recipient": {"chat_id": owner_chat_id, "chat_type": "dialog"},
                "body": {"text": "/bind_alerts_chat admin12345"},
            },
        }
    )
    async with container.session_factory() as session:
        alert_chat = await session.get(BotChat, owner_chat_id)
        assert alert_chat is not None
        assert alert_chat.chat_purpose == ChatPurpose.FOREMAN_ALERTS.value

    # 3. Привязываем групповой чат рабочих через /bind_dispatch_chat
    await container.bot_router.handle_update(
        {
            "update_id": "bind-disp-1",
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": owner_max_uid, "first_name": "Леонид"},
                "recipient": {"chat_id": brigade_chat_id, "chat_type": "chat"},
                "body": {"text": "/bind_dispatch_chat"},
            },
        }
    )

    # 4. Клиент оформляет заказ на сайте -> владельцу мгновенно приходит уведомление в MAX
    resp = await http.post(
        "/api/v1/public/orders",
        json={
            "service_type": "snow_yard",
            "zone_code": "malakhovka",
            "area_m2": 120,
            "address_text": "ул. Красковская, д. 7",
            "client_name": "Иван",
            "client_phone": "+7 (999) 123-45-67",
            "consent_pdn": True,
            "consent_offer": True,
        },
    )
    assert resp.status_code == 201, resp.text
    order_id = resp.json()["order_id"]
    pub_num = resp.json()["public_number"]

    async with container.session_factory() as session:
        alert_msgs = list(
            (
                await session.execute(
                    select(SimulatorMessage).where(SimulatorMessage.chat_id == owner_chat_id)
                )
            )
            .scalars()
            .all()
        )
    alert_card = next((m for m in alert_msgs if f"НОВАЯ ЗАЯВКА #{pub_num}" in m.text), None)
    assert alert_card is not None
    assert "+79991234567" in alert_card.text
    alert_btns = [
        b
        for att in (alert_card.attachments_json or [])
        if att.get("type") == "inline_keyboard"
        for row in att.get("payload", {}).get("buttons", [])
        for b in row
    ]
    assert any(b.get("payload") == f"disp:publish:{order_id}" for b in alert_btns)

    # 5. Владелец нажимает кнопку "Отправить в чат рабочих" в MAX -> карточка уходит бригаде
    await container.bot_router.handle_update(
        {
            "update_id": "disp-pub-1",
            "update_type": "message_callback",
            "callback": {
                "callback_id": "cb-disp-1",
                "payload": f"disp:publish:{order_id}",
                "user": {"user_id": owner_max_uid, "first_name": "Леонид"},
            },
            "message": {"recipient": {"chat_id": owner_chat_id, "chat_type": "dialog"}},
        }
    )

    async with container.session_factory() as session:
        order = await session.get(Order, uuid.UUID(order_id))
        assert order is not None
        assert order.status == "searching_executor"
        assert order.dispatch_chat_id is not None
        worker_msgs = list(
            (
                await session.execute(
                    select(SimulatorMessage).where(
                        SimulatorMessage.chat_id == order.dispatch_chat_id
                    )
                )
            )
            .scalars()
            .all()
        )
    worker_card = next((m for m in worker_msgs if f"#{pub_num}" in m.text), None)
    assert worker_card is not None
    # Номер телефона клиента строго скрыт в общем чате рабочих!
    assert "+79991234567" not in worker_card.text
    worker_btns = [
        b
        for att in (worker_card.attachments_json or [])
        if att.get("type") == "inline_keyboard"
        for row in att.get("payload", {}).get("buttons", [])
        for b in row
    ]
    assert any(b.get("payload") == f"ord:take:{order_id}" for b in worker_btns)

    # 6. Повторная отправка / отправка из веб-админки POST /api/v1/admin/orders/{id}/dispatch тоже работает
    login = await http.post(
        "/api/v1/admin/auth/login",
        json={"email": "owner@sneg.local", "password": "admin12345"},
    )
    assert login.status_code == 200
    disp_resp = await http.post(f"/api/v1/admin/orders/{order_id}/dispatch")
    assert disp_resp.status_code == 200
    assert disp_resp.json()["order_status"] == "searching_executor"


