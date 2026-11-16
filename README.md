# Сервис «Снег-Диспетчер»

Автоматизированный сервис приёма и диспетчеризации заказов на уборку снега, расчистку дорог, срочную помощь застрявшим автомобилям и бытовые услуги в мессенджере **MAX** (Малаховка · Томилино · Красково · Быково · Лыткарино, с масштабированием на Октябрьский и Люберцы).

## Документация проекта

| Файл | Описание |
|---|---|
| [`docs/00_audit_and_decisions.md`](./docs/00_audit_and_decisions.md) | Аудит исходного КП/ТЗ (v1.0), сверка с актуальной документацией MAX Bot API (`platform-api2.max.ru`) и ЮKassa СБП, реестр архитектурных решений (ADR-001..010) и список вопросов для владельца, бухгалтера и юриста. |
| [`docs/01_commercial_proposal_and_tz.md`](./docs/01_commercial_proposal_and_tz.md) | **Основной мастер-документ (v2.0):** Коммерческое предложение (Часть A1–A8) и полное Техническое задание (Часть B1–B14). |
| [`docs/02_database_schema.sql`](./docs/02_database_schema.sql) | Эталонная DDL-схема PostgreSQL 16 (23 таблицы, enums, индексы и seed-данные). |
| [`.env.example`](./.env.example) | Шаблон переменных окружения для локальной разработки и production-развёртывания. |

## Реализованная архитектура

```text
src/
├── core/         # Конфигурация (с prod-защитами), enums, исключения, JWT/Argon2/IP-фильтры, DI-контейнер
├── domain/       # Чистая бизнес-логика: Decimal-калькулятор (округление до 50 ₽), геозоны, статусная машина
├── infra/        # SQLAlchemy 2.0 (23 таблицы, PG16 + SQLite dev), seed, Local/S3 хранилище, ARQ worker
├── services/     # MessengerTransport (MaxApiClient + SimulatorTransport), PaymentProvider (YooKassa + Mock),
│                 # WeatherProvider (Open-Meteo + Mock), OrderOrchestrator (атомарный «Беру», фото-гейты, НПД)
├── bot/          # FSMStorage (Redis / InMemory), клавиатуры MAX, единый маршрутизатор MAX Update
└── api/          # FastAPI: /webhooks/max, /webhooks/yookassa, /mock-pay, /api/v1/admin/*, /api/v1/simulator/*
web/              # UI: стартовая (/), мультичат-симулятор MAX (/simulator), веб-админка (/admin)
migrations/       # Alembic (0001_initial_schema)
docker/           # Dockerfile (Python 3.12 + сертификаты Минцифры РФ), nginx HTTPS, docker-compose.yml
tests/            # Unit, integration (20-поточная гонка «Беру», вебхуки, безопасность) и E2E тесты
```

## Быстрый старт (локально с симулятором и админкой)

Без токена MAX, без ЮKassa, без PostgreSQL/Redis — из коробки поднимается на SQLite + InMemory FSM:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8000
```

После запуска откройте в браузере:
- **`/`** — главная страница со статусом подсистем
- **`/simulator`** — интерактивный мультичат-симулятор MAX (Клиенты, Общий чат бригады, Исполнители, Бригадир, Mock-СБП, гонка «Беру», фото «ДО/ПОСЛЕ», таймеры, снегопад)
- **`/admin`** — веб-админка (dev-доступ: владелец `owner@sneg.local` / `admin12345`, бригадир `foreman@sneg.local` / `foreman12345`)
- **`/docs`** — Swagger / OpenAPI 3.1

## Запуск в production (Docker Compose + PostgreSQL 16 + Redis + ARQ + nginx)

```bash
cp .env.example .env
# Заполните MAX_BOT_TOKEN, MAX_WEBHOOK_SECRET, YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY,
# ADMIN_JWT_SECRET, PII_ENCRYPTION_KEY и переключите:
# APP_ENV=production, MESSENGER_TRANSPORT=max, PAYMENT_PROVIDER=yookassa, ENABLE_SIMULATOR=false
docker compose up -d --build
docker compose exec api alembic upgrade head
```

## Проверка качества и тесты

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -v
```
