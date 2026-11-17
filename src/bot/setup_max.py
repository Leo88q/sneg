"""CLI-утилита проверки токена и регистрации вебхука MAX Bot API (platform-api2.max.ru).

Запуск внутри Docker-контейнера (где установлены сертификаты Минцифры РФ):
    docker compose exec api python -m src.bot.setup_max
    docker compose exec api python -m src.bot.setup_max --check-only
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from src.core.config import Settings, get_settings
from src.services.messenger.max_client import MaxApiClient

MAX_WEBHOOK_UPDATE_TYPES: list[str] = [
    "bot_started",
    "message_created",
    "message_callback",
    "bot_added",
    "bot_removed",
]
_SECRET_RE = re.compile(r"^[a-zA-Z0-9_-]{5,256}$")


def validate_max_setup_settings(settings: Settings) -> tuple[str, str]:
    """Проверяет настройки MAX перед регистрацией вебхука и возвращает (webhook_url, secret)."""
    token = settings.max_bot_token.strip()
    if not token or token.startswith("replace-with"):
        raise ValueError("MAX_BOT_TOKEN не заполнен в .env")

    base_url = settings.public_base_url.strip().rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError(
            "PUBLIC_BASE_URL должен начинаться с https:// (например https://chistodvor.ru)"
        )

    secret = settings.max_webhook_secret.strip()
    if not _SECRET_RE.match(secret) or secret.startswith("dev-") or secret.startswith("replace-with"):
        raise ValueError(
            "MAX_WEBHOOK_SECRET должен содержать 5–256 символов [A-Za-z0-9_-] и не быть тестовым"
        )

    return f"{base_url}/webhooks/max", secret


async def run_setup(*, check_only: bool = False) -> int:
    settings = get_settings()
    try:
        webhook_url, secret = validate_max_setup_settings(settings)
    except ValueError as exc:
        print(f"❌ Ошибка конфигурации .env: {exc}", file=sys.stderr)
        return 1

    client = MaxApiClient(
        token=settings.max_bot_token.strip(),
        base_url=settings.max_api_base_url,
        ca_bundle_path=settings.max_ca_bundle_path,
    )
    try:
        me = await client.get_me()
        bot_link = f"https://max.ru/{me.username}" if me.username else "(username не указан)"
        print(f"✅ Токен MAX валиден: {me.first_name} (user_id={me.user_id}, username={me.username})")
        print(f"🔗 Ссылка на бота для MAX_BOT_URL: {bot_link}")

        if not check_only:
            ok = await client.setup_webhook(
                url=webhook_url,
                secret=secret,
                update_types=MAX_WEBHOOK_UPDATE_TYPES,
            )
            if not ok:
                print("❌ MAX вернул success=false при регистрации вебхука", file=sys.stderr)
                return 2
            print(f"✅ Вебхук зарегистрирован: {webhook_url}")

        subs = await client.list_webhooks()
        print(f"📡 Активные подписки MAX ({len(subs)}):")
        for s in subs:
            print(f"   - {s.get('url')} | events={s.get('update_types')}")
        return 0
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Регистрация и проверка вебхука MAX Bot API")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Только проверить GET /me и текущие подписки без POST /subscriptions",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_setup(check_only=args.check_only)))


if __name__ == "__main__":
    main()
