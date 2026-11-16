"""Конфигурация приложения (pydantic-settings, синхронизирована с .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Приложение
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    public_base_url: str = ""
    timezone: str = "Europe/Moscow"
    secret_key: str = "dev-secret-key-change-me-in-production-0123456789abcdef"
    log_level: str = "INFO"

    # Симулятор MAX-бота (строго false в production)
    enable_simulator: bool = True

    # MAX Bot API (platform-api2.max.ru)
    messenger_transport: Literal["max", "simulator"] = "simulator"
    max_api_base_url: str = "https://platform-api2.max.ru"
    max_bot_token: str = ""
    max_update_mode: Literal["webhook", "polling"] = "webhook"
    max_webhook_secret: str = "dev-max-webhook-secret"
    max_ca_bundle_path: str = ""
    max_global_rps: int = 25
    max_per_chat_rps: int = 2
    max_dispatch_chat_id: int | None = None

    # БД и Redis
    database_url: str = "sqlite+aiosqlite:///./var/sneg.db"
    redis_url: str = ""
    fsm_ttl_seconds: int = 21600

    # Платежи
    payment_provider: Literal["yookassa", "mock"] = "mock"
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_force_sbp: bool = True
    yookassa_tax_system_code: int = 2
    yookassa_vat_code: int = 1
    yookassa_trusted_ips: str = (
        "185.71.76.0/27,185.71.77.0/27,77.75.153.0/25,"
        "77.75.156.11/32,77.75.156.35/32,77.75.154.128/25,2a02:5180::/32"
    )

    # Хранилище файлов
    storage_provider: Literal["s3", "local"] = "local"
    local_storage_dir: str = "./var/media"
    s3_endpoint_url: str = "https://storage.yandexcloud.net"
    s3_bucket: str = "sneg-media"
    s3_region: str = "ru-central1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_presigned_ttl_seconds: int = 900

    # Погода
    weather_provider: Literal["open_meteo", "mock"] = "mock"
    weather_api_key: str = ""

    # Админка и юридические ссылки
    admin_jwt_secret: str = "dev-admin-jwt-secret-change-me-in-production-0123456789"
    admin_jwt_ttl_minutes: int = 480
    initial_owner_email: str = "owner@sneg.local"
    initial_owner_password: str = Field(default="admin12345")
    public_offer_url: str = "https://sneg.example.ru/legal/offer"
    privacy_policy_url: str = "https://sneg.example.ru/legal/privacy"
    executor_offer_url: str = "https://sneg.example.ru/legal/executor-offer"

    # Наблюдаемость
    sentry_dsn: str = ""
    alerts_max_chat_id: int | None = None

    @model_validator(mode="after")
    def validate_production_guards(self) -> Settings:
        if self.app_env == "production":
            if self.enable_simulator:
                raise ValueError("ENABLE_SIMULATOR must be false in production")
            if self.payment_provider == "mock":
                raise ValueError("PAYMENT_PROVIDER=mock is forbidden in production")
            if self.messenger_transport == "simulator":
                raise ValueError("MESSENGER_TRANSPORT=simulator is forbidden in production")
        return self

    @property
    def yookassa_trusted_cidrs(self) -> list[str]:
        return [item.strip() for item in self.yookassa_trusted_ips.split(",") if item.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
