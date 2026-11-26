from __future__ import annotations

import httpx
import pytest

from src.core.config import Settings
from src.core.security import verify_webhook_secret
from src.main import create_app


def test_empty_max_webhook_secret_never_accepts_requests() -> None:
    assert not verify_webhook_secret("", None)
    assert not verify_webhook_secret("", "")
    assert not verify_webhook_secret("", "anything")
    assert not verify_webhook_secret(" ", " ")
    assert not verify_webhook_secret("valid", None)
    assert verify_webhook_secret("valid", "valid")


@pytest.mark.asyncio
async def test_browser_cors_is_same_origin_by_default_and_opt_in(test_settings: Settings) -> None:
    app = create_app(settings=test_settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
        response = await http.get("/health", headers={"Origin": "https://malicious.example"})
        assert "access-control-allow-origin" not in response.headers
        preflight = await http.options("/api/v1/admin/auth/login", headers={
            "Origin": "https://malicious.example", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        assert "access-control-allow-origin" not in preflight.headers
    await app.state.container.shutdown()

    cfg = test_settings.model_copy(update={"cors_allowed_origins": "https://trusted.example"})
    app = create_app(settings=cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
        good = await http.options("/api/v1/admin/auth/login", headers={
            "Origin": "https://trusted.example", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        assert good.status_code == 200
        assert good.headers["access-control-allow-origin"] == "https://trusted.example"
        bad = await http.options("/api/v1/admin/auth/login", headers={
            "Origin": "https://malicious.example", "Access-Control-Request-Method": "POST",
        })
        assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers
    await app.state.container.shutdown()
    with pytest.raises(ValueError, match="wildcard"):
        create_app(settings=test_settings.model_copy(update={"cors_allowed_origins": "*"}))


@pytest.mark.asyncio
async def test_empty_secret_rejects_real_webhook_endpoint(test_settings: Settings) -> None:
    settings = test_settings.model_copy(update={"max_webhook_secret": ""})
    app = create_app(settings=settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
        for headers in ({}, {"X-Max-Bot-Api-Secret": "anything"}):
            response = await http.post("/webhooks/max", json={"update_type": "bot_started"}, headers=headers)
            assert response.status_code == 401
    await app.state.container.shutdown()
