"""DI-контейнер приложения с управляемым жизненным циклом."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.bot.fsm import FSMStorage, InMemoryFSMStorage, RedisFSMStorage
from src.bot.router import BotRouter
from src.core.config import Settings
from src.infra.db import models  # noqa: F401
from src.infra.db.seed import seed_initial_data
from src.infra.db.session import Base, build_engine, build_session_factory
from src.infra.storage.base import FileStorage
from src.infra.storage.local import LocalFileStorage
from src.infra.storage.s3 import S3FileStorage
from src.services.messenger.base import MessengerTransport
from src.services.messenger.max_client import MaxApiClient
from src.services.messenger.simulator_transport import SimulatorTransport
from src.services.orders.orchestrator import OrderOrchestrator
from src.services.payments.base import PaymentProvider
from src.services.payments.mock import MockPaymentProvider
from src.services.payments.yookassa import YooKassaProvider
from src.services.weather.provider import MockWeatherProvider, OpenMeteoWeatherProvider


class AppContainer:
    def __init__(
        self,
        settings: Settings,
        *,
        messenger: MessengerTransport | None = None,
        payment_provider: PaymentProvider | None = None,
        storage: FileStorage | None = None,
    ) -> None:
        self.settings = settings
        self.engine: AsyncEngine = build_engine(settings.database_url, echo=False)
        self.session_factory: async_sessionmaker[AsyncSession] = build_session_factory(self.engine)
        self.redis_client = None

        if settings.redis_url:
            import redis.asyncio as aioredis

            self.redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
            self.fsm: FSMStorage = RedisFSMStorage(
                self.redis_client, ttl_seconds=settings.fsm_ttl_seconds
            )
        else:
            self.fsm = InMemoryFSMStorage(ttl_seconds=settings.fsm_ttl_seconds)

        if storage is not None:
            self.storage: FileStorage = storage
        elif settings.storage_provider == "s3":
            self.storage = S3FileStorage(
                endpoint_url=settings.s3_endpoint_url,
                bucket=settings.s3_bucket,
                region=settings.s3_region,
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
            )
        else:
            self.storage = LocalFileStorage(settings.local_storage_dir)

        if messenger is not None:
            self.messenger: MessengerTransport = messenger
        elif settings.messenger_transport == "max":
            self.messenger = MaxApiClient(
                token=settings.max_bot_token,
                base_url=settings.max_api_base_url,
                ca_bundle_path=settings.max_ca_bundle_path,
                global_rps=settings.max_global_rps,
                per_chat_rps=settings.max_per_chat_rps,
            )
        else:
            self.messenger = SimulatorTransport(self.session_factory)

        if payment_provider is not None:
            self.payment_provider: PaymentProvider = payment_provider
        elif settings.payment_provider == "yookassa":
            self.payment_provider = YooKassaProvider(
                shop_id=settings.yookassa_shop_id,
                secret_key=settings.yookassa_secret_key,
                force_sbp=settings.yookassa_force_sbp,
                tax_system_code=settings.yookassa_tax_system_code,
                vat_code=settings.yookassa_vat_code,
                trusted_cidrs=settings.yookassa_trusted_cidrs,
            )
        else:
            self.payment_provider = MockPaymentProvider(public_base_url=settings.public_base_url)

        self.weather_provider = (
            OpenMeteoWeatherProvider()
            if settings.weather_provider == "open_meteo"
            else MockWeatherProvider()
        )

        self.orchestrator = OrderOrchestrator(
            session_factory=self.session_factory,
            settings=settings,
            messenger=self.messenger,
            payment_provider=self.payment_provider,
            storage=self.storage,
        )
        self.bot_router = BotRouter(
            session_factory=self.session_factory,
            settings=settings,
            fsm=self.fsm,
            messenger=self.messenger,
            orchestrator=self.orchestrator,
        )

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.session_factory() as session:
            await seed_initial_data(session, self.settings)

    async def shutdown(self) -> None:
        await self.messenger.close()
        await self.payment_provider.close()
        if self.redis_client is not None:
            await self.redis_client.aclose()
        await self.engine.dispose()
