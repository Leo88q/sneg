"""ARQ Worker Settings и cron-задачи (ТЗ B11)."""

from __future__ import annotations

import logging
from typing import Any

from arq.connections import RedisSettings
from arq.cron import cron

from src.core.config import get_settings
from src.infra.db.session import build_engine, build_session_factory
from src.infra.storage.local import LocalFileStorage
from src.infra.storage.s3 import S3FileStorage
from src.services.messenger.max_client import MaxApiClient
from src.services.messenger.simulator_transport import SimulatorTransport
from src.services.orders.orchestrator import OrderOrchestrator
from src.services.payments.mock import MockPaymentProvider
from src.services.payments.yookassa import YooKassaProvider

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    messenger = (
        MaxApiClient(
            token=settings.max_bot_token,
            base_url=settings.max_api_base_url,
            ca_bundle_path=settings.max_ca_bundle_path,
            global_rps=settings.max_global_rps,
            per_chat_rps=settings.max_per_chat_rps,
        )
        if settings.messenger_transport == "max"
        else SimulatorTransport(session_factory)
    )
    payment_provider = (
        YooKassaProvider(
            shop_id=settings.yookassa_shop_id,
            secret_key=settings.yookassa_secret_key,
            force_sbp=settings.yookassa_force_sbp,
            tax_system_code=settings.yookassa_tax_system_code,
            vat_code=settings.yookassa_vat_code,
            trusted_cidrs=settings.yookassa_trusted_cidrs,
        )
        if settings.payment_provider == "yookassa"
        else MockPaymentProvider(public_base_url=settings.public_base_url)
    )
    storage = (
        S3FileStorage(
            endpoint_url=settings.s3_endpoint_url,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        )
        if settings.storage_provider == "s3"
        else LocalFileStorage(settings.local_storage_dir)
    )
    ctx["engine"] = engine
    ctx["messenger"] = messenger
    ctx["payment_provider"] = payment_provider
    ctx["orchestrator"] = OrderOrchestrator(
        session_factory=session_factory,
        settings=settings,
        messenger=messenger,
        payment_provider=payment_provider,
        storage=storage,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["messenger"].close()
    await ctx["payment_provider"].close()
    await ctx["engine"].dispose()


async def run_timers_job(ctx: dict[str, Any]) -> dict[str, int]:
    orchestrator: OrderOrchestrator = ctx["orchestrator"]
    res = await orchestrator.run_expired_timers(force_all=False)
    logger.info("ARQ run_timers_job result: %s", res)
    return res


class WorkerSettings:
    functions = [run_timers_job]
    cron_jobs = [
        cron(run_timers_job, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(
        get_settings().redis_url or "redis://redis:6379/0"
    )
