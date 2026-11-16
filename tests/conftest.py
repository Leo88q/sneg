from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from src.core.config import Settings
from src.core.container import AppContainer
from src.main import create_app


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    db_file = tmp_path / "test_sneg.db"
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        app_env="development",
        app_debug=True,
        app_base_url="http://testserver",
        database_url=f"sqlite+aiosqlite:///{db_file}",
        redis_url="",
        messenger_transport="simulator",
        enable_simulator=True,
        max_bot_token="test-token",
        max_webhook_secret="test-max-secret-12345",
        payment_provider="mock",
        file_storage_backend="local",
        local_media_dir=str(media_dir),
        weather_provider="mock",
        admin_jwt_secret="test-jwt-secret-key-at-least-32-chars!!",
        admin_seed_email="owner@sneg.local",
        admin_seed_password="admin12345",
        pii_encryption_key="test-pii-key",
    )


@pytest_asyncio.fixture
async def container(test_settings: Settings) -> AsyncIterator[AppContainer]:
    c = AppContainer(test_settings)
    await c.startup()
    try:
        yield c
    finally:
        await c.shutdown()


@pytest_asyncio.fixture
async def client(test_settings: Settings) -> AsyncIterator[tuple[httpx.AsyncClient, AppContainer]]:
    app = create_app(settings=test_settings)
    c: AppContainer = app.state.container
    await c.startup()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        try:
            yield ac, c
        finally:
            await c.shutdown()
