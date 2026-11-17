"""Тесты соответствия 20 требованиям законодательства РФ, self-hosted шрифтов и DSR (152-ФЗ)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from src.core.config import Settings
from src.infra.db.models import DataSubjectRequest, User


@pytest.mark.asyncio
async def test_empty_optional_chat_id_env_vars_parse_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Регрессия: пустые MAX_DISPATCH_CHAT_ID= и пустые LEGAL_* в .env не ломают старт и реквизиты."""
    monkeypatch.setenv("MAX_DISPATCH_CHAT_ID", "")
    monkeypatch.setenv("ALERTS_MAX_CHAT_ID", "   ")
    monkeypatch.setenv("LEGAL_INN", "")
    monkeypatch.setenv("LEGAL_OGRNIP", "")
    monkeypatch.setenv("LEGAL_PHONE", "")
    monkeypatch.setenv("LEGAL_EMAIL", "")
    cfg = Settings()
    assert cfg.max_dispatch_chat_id is None
    assert cfg.alerts_max_chat_id is None
    assert cfg.legal_inn == "231521684300"
    assert cfg.legal_ogrnip == "321344300084915"
    assert cfg.legal_phone == "+7 (977) 924-02-93"
    assert cfg.legal_email == "holmatovleonid25@gmail.com"


@pytest.mark.asyncio
async def test_all_legal_pages_and_self_hosted_fonts_zero_google_cdn(client: Any) -> None:
    """Все юридические страницы, главная и шрифты доступны локально без fonts.googleapis.com."""
    http, _container = client
    pages = [
        "/",
        "/admin",
        "/simulator",
        "/pay/return?order=1001",
        "/legal",
        "/legal/privacy",
        "/legal/consent",
        "/legal/offer",
        "/legal/refunds",
        "/legal/executor-offer",
        "/legal/cookies",
        "/legal/requisites",
        "/legal/licenses",
        "/legal/my-data",
    ]
    for path in pages:
        resp = await http.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"
        assert "fonts.googleapis.com" not in resp.text, f"External Google Fonts found in {path}"
        assert "fonts.gstatic.com" not in resp.text, f"External gstatic found in {path}"
        assert "/static/fonts.css" in resp.text, f"Self-hosted fonts.css missing in {path}"

    # Проверяем, что реальные реквизиты ИП отображаются на ключевых страницах и в API
    for legal_path in ("/legal/requisites", "/legal/privacy", "/legal/offer", "/legal/consent"):
        doc = await http.get(legal_path)
        assert "ИП Холматов Леонид Икрамович" in doc.text
        assert "231521684300" in doc.text
        assert "321344300084915" in doc.text
        assert "+7 (977) 924-02-93" in doc.text
        assert "holmatovleonid25@gmail.com" in doc.text

    meta = await http.get("/api/v1/legal/public-meta")
    assert meta.status_code == 200
    mj = meta.json()
    assert mj["inn"] == "231521684300"
    assert mj["ogrnip"] == "321344300084915"
    assert mj["phone"] == "+7 (977) 924-02-93"
    assert mj["email"] == "holmatovleonid25@gmail.com"

    # Проверяем сами локальные шрифты, OFL-лицензии и фирменные иконки сайта
    for asset in [
        "/favicon.ico",
        "/apple-touch-icon.png",
        "/static/favicon-32x32.png",
        "/static/favicon-192x192.png",
        "/static/brand-logo.png",
        "/static/chistodvor-bot-logo.png",
        "/static/fonts.css",
        "/static/fonts/unbounded-cyrillic-700-normal.woff2",
        "/static/fonts/golos-text-cyrillic-400-normal.woff2",
        "/static/fonts/jetbrains-mono-cyrillic-500-normal.woff2",
        "/static/fonts/licenses/OFL-Unbounded.txt",
        "/static/fonts/licenses/OFL-Golos-Text.txt",
        "/static/fonts/licenses/OFL-JetBrains-Mono.txt",
    ]:
        r = await http.get(asset)
        assert r.status_code == 200, f"Asset missing: {asset}"


@pytest.mark.asyncio
async def test_dsr_validation_creation_unsubscribe_and_admin_anonymization(
    client: Any,
) -> None:
    """Полный цикл 152-ФЗ: непредзаполненные галочки -> удаление/отписка -> обезличивание в админке."""
    http, container = client
    # 1. Без явных согласий (п. 6, 17) сервер обязан вернуть 400
    bad = await http.post(
        "/api/v1/legal/data-requests",
        json={
            "request_type": "delete_data",
            "contact_value": "10001",
            "consent_accepted": False,
            "age_confirmed": True,
        },
    )
    assert bad.status_code == 400

    bad_age = await http.post(
        "/api/v1/legal/data-requests",
        json={
            "request_type": "delete_data",
            "contact_value": "10001",
            "consent_accepted": True,
            "age_confirmed": False,
        },
    )
    assert bad_age.status_code == 400

    # 2. Мгновенная отписка от рассылок (п. 18) для client_2 (max_user_id=10002)
    unsub = await http.post(
        "/api/v1/legal/data-requests",
        json={
            "request_type": "unsubscribe_marketing",
            "contact_value": "10002",
            "consent_accepted": True,
            "age_confirmed": True,
        },
    )
    assert unsub.status_code == 201
    assert unsub.json()["status"] == "completed"
    assert unsub.json()["ticket_code"].startswith("DSR-")

    # 3. Запрос на удаление данных (п. 20) для client_1 (max_user_id=10001)
    del_resp = await http.post(
        "/api/v1/legal/data-requests",
        json={
            "request_type": "delete_data",
            "contact_value": "10001",
            "details": "Прошу удалить мои ПДн",
            "consent_accepted": True,
            "age_confirmed": True,
        },
    )
    assert del_resp.status_code == 201
    del_data = del_resp.json()
    assert del_data["status"] == "pending"

    # 4. Вход владельца в админку, проверка списка DSR и обезличивание пользователя
    login = await http.post(
        "/api/v1/admin/auth/login",
        json={"email": "owner@sneg.local", "password": "admin12345"},
    )
    assert login.status_code == 200

    # Проверка, что рассылка исключает отписавшегося client_2
    bc = await http.post(
        "/api/v1/admin/broadcasts",
        json={"target_role": "client", "text": "Сезонное предложение"},
    )
    assert bc.status_code == 200
    assert bc.json()["excluded_unsubscribed"] >= 1

    dsr_list = await http.get("/api/v1/admin/data-requests")
    assert dsr_list.status_code == 200
    items = dsr_list.json()
    target = next(i for i in items if i["ticket_code"] == del_data["ticket_code"])
    assert target["user_id"] is not None

    resolve = await http.post(
        f"/api/v1/admin/data-requests/{target['id']}/resolve",
        json={
            "status": "completed",
            "anonymize_user": True,
            "resolution_note": "Обезличено в рамках теста 152-ФЗ",
        },
    )
    assert resolve.status_code == 200
    assert resolve.json()["anonymized"] is True

    # Убеждаемся, что ПДн реально очищены в БД
    async with container.session_factory() as session:
        u = (
            await session.execute(select(User).where(User.max_user_id == 10001))
        ).scalar_one()
        assert u.first_name == "Удалённый пользователь"
        assert u.bot_phone is None
        assert u.email is None


@pytest.mark.asyncio
async def test_bot_privacy_unsubscribe_and_delete_commands(
    client: Any,
) -> None:
    """Команды /privacy, /unsubscribe, /delete_my_data в MAX-боте."""
    http, container = client
    for cmd in ("/privacy", "/unsubscribe", "/delete_my_data"):
        r = await http.post(
            "/api/v1/simulator/send-message",
            json={"persona": "client_2", "text": cmd},
        )
        assert r.status_code == 200

    async with container.session_factory() as session:
        reqs = list(
            (
                await session.execute(
                    select(DataSubjectRequest).where(
                        DataSubjectRequest.source == "max_bot",
                        DataSubjectRequest.max_user_id == 10002,
                    )
                )
            )
            .scalars()
            .all()
        )
        types = {r.request_type for r in reqs}
        assert "unsubscribe_marketing" in types
        assert "delete_data" in types
