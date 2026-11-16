"""Точка входа FastAPI-приложения «Снег-Диспетчер»."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.admin import media_router
from src.api.admin import router as admin_router
from src.api.health import router as health_router
from src.api.mock_pay import router as mock_pay_router
from src.api.simulator import router as simulator_router
from src.api.webhooks import router as webhooks_router
from src.core.config import Settings, get_settings
from src.core.container import AppContainer
from src.core.exceptions import AppError
from src.core.logging import configure_logging
from src.infra.storage.base import FileStorage
from src.services.messenger.base import MessengerTransport
from src.services.payments.base import PaymentProvider

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(
    *,
    settings: Settings | None = None,
    messenger: MessengerTransport | None = None,
    payment_provider: PaymentProvider | None = None,
    storage: FileStorage | None = None,
) -> FastAPI:
    cfg = settings or get_settings()
    configure_logging(cfg.log_level)

    container = AppContainer(
        cfg,
        messenger=messenger,
        payment_provider=payment_provider,
        storage=storage,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container
        await container.startup()
        try:
            yield
        finally:
            await container.shutdown()

    app = FastAPI(
        title="Снег-Диспетчер API",
        version="2.0.0",
        description="MAX-бот, бэкенд диспетчеризации, ЮKassa СБП + 54-ФЗ, веб-админка и симулятор",
        lifespan=lifespan,
        docs_url="/docs" if cfg.app_env != "production" else None,
        redoc_url="/redoc" if cfg.app_env != "production" else None,
    )
    app.state.container = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    app.include_router(health_router)
    app.include_router(webhooks_router)
    app.include_router(admin_router)
    app.include_router(media_router)
    if cfg.payment_provider == "mock":
        app.include_router(mock_pay_router)
    if cfg.enable_simulator:
        app.include_router(simulator_router)

    if WEB_DIR.exists():
        static_dir = WEB_DIR / "static"
        if static_dir.exists():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index_page() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    async def admin_page() -> FileResponse:
        return FileResponse(WEB_DIR / "admin.html")

    @app.get("/simulator", response_class=HTMLResponse, include_in_schema=False)
    async def simulator_page() -> FileResponse:
        if not cfg.enable_simulator:
            return HTMLResponse("<h1>Simulator disabled</h1>", status_code=404)  # type: ignore[return-value]
        return FileResponse(WEB_DIR / "simulator.html")

    @app.get("/pay/return", response_class=HTMLResponse, include_in_schema=False)
    async def pay_return_page(order: str = "") -> HTMLResponse:
        return HTMLResponse(
            f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Обработка оплаты · Снег-Диспетчер</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link
    href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700&family=JetBrains+Mono:wght@500;700&family=Unbounded:wght@600;700&display=swap"
    rel="stylesheet"
  />
  <link rel="stylesheet" href="/static/styles.css" />
</head>
<body style="display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px">
  <div class="ice-panel ice-interactive" style="max-width:440px;width:100%;padding:28px;text-align:center">
    <div class="section-kicker" style="justify-content:center;margin-bottom:10px">СТАТУС СБП</div>
    <h2 style="margin:0 0 10px;font-size:20px">Оплата заказа <span class="mono">#{order}</span> обрабатывается</h2>
    <p style="color:var(--text-frost);margin:0 0 22px;font-size:13.5px">
      Вернитесь в MAX-бот — статус заказа обновится автоматически сразу после подтверждения банком.
    </p>
    <div style="display:flex;gap:10px;justify-content:center">
      <a class="btn btn-primary btn-sm" href="/simulator">Открыть симулятор</a>
      <a class="btn btn-sm" href="/admin">Пульт админа</a>
    </div>
  </div>
  <script src="/static/frost-fx.js"></script>
</body>
</html>"""
        )

    return app


app = create_app()
