-- =============================================================================
-- Сервис «Снег-Диспетчер» — Эталонная схема БД PostgreSQL 16 (ТЗ v2.0, раздел B4)
-- Назначение: спецификация для генерации первой миграции Alembic на Этапе 0.
-- Требования:
--   * Все денежные суммы — NUMERIC(12, 2), валюта по умолчанию RUB.
--   * Все временные метки — TIMESTAMPTZ (хранение в UTC).
--   * Атомарный захват заказа «Беру» реализуется через SELECT ... FOR UPDATE
--     по строке orders + вставку в order_assignments.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- -----------------------------------------------------------------------------
-- 1. Перечисления (ENUM)
-- -----------------------------------------------------------------------------

CREATE TYPE user_role AS ENUM ('client', 'executor', 'foreman', 'admin', 'owner');
CREATE TYPE executor_status AS ENUM ('pending_verification', 'active', 'suspended', 'blocked');
CREATE TYPE admin_role AS ENUM ('foreman', 'admin', 'owner');
CREATE TYPE consent_type AS ENUM (
    'pdn',
    'client_offer',
    'executor_offer',
    'phone_transfer',
    'safety_briefing'
);

CREATE TYPE chat_purpose AS ENUM ('dispatch_pool', 'foreman_alerts', 'other');
CREATE TYPE geo_zone_tier AS ENUM ('core', 'extended', 'planned');

CREATE TYPE service_type AS ENUM (
    'snow_yard',
    'snow_road',
    'car_rescue',
    'yard_help',
    'movers',
    'roof_cleaning',
    'reagents'
);

CREATE TYPE cleaning_method AS ENUM (
    'manual',
    'snowblower_or_miniloader',
    'tractor'
);

CREATE TYPE urgency_level AS ENUM ('today', 'within_3h', 'now_or_night');
CREATE TYPE contact_mode AS ENUM ('bot_proxy', 'direct_phone');

CREATE TYPE order_status AS ENUM (
    'draft',
    'awaiting_advance',
    'searching_executor',
    'partially_assigned',
    'assigned',
    'en_route',
    'arrived',
    'price_adjustment_pending',
    'in_progress',
    'awaiting_acceptance',
    'awaiting_final_payment',
    'completed',
    'disputed',
    'canceled_timeout',
    'canceled_by_client',
    'canceled_by_service'
);

CREATE TYPE order_payment_status AS ENUM (
    'unpaid',
    'advance_pending',
    'advance_paid',
    'final_pending',
    'fully_paid',
    'partially_refunded',
    'refunded'
);

CREATE TYPE assignment_role AS ENUM ('primary', 'helper');
CREATE TYPE assignment_status AS ENUM (
    'assigned',
    'en_route',
    'arrived',
    'in_progress',
    'completed',
    'released_by_executor',
    'removed_by_foreman'
);

CREATE TYPE media_stage AS ENUM (
    'client_problem',
    'before',
    'after',
    'price_adjustment',
    'dispute'
);

CREATE TYPE price_adjustment_status AS ENUM ('pending', 'approved', 'rejected', 'expired');

CREATE TYPE payment_purpose AS ENUM ('advance', 'final', 'full_prepayment', 'subscription');
CREATE TYPE payment_provider_type AS ENUM ('yookassa', 'mock');
CREATE TYPE payment_status AS ENUM ('pending', 'succeeded', 'canceled');
CREATE TYPE refund_status AS ENUM ('pending', 'succeeded', 'canceled');

CREATE TYPE dispute_status AS ENUM (
    'open',
    'in_review',
    'resolved_rework',
    'resolved_discount',
    'resolved_refund',
    'rejected'
);

CREATE TYPE ledger_entry_type AS ENUM (
    'order_earning',
    'dispatch_compensation',
    'urgency_bonus',
    'penalty',
    'manual_adjustment'
);

CREATE TYPE ledger_entry_status AS ENUM ('accrued', 'included_in_batch', 'paid', 'voided');
CREATE TYPE payout_batch_status AS ENUM ('draft', 'paid');

CREATE TYPE subscription_plan_type AS ENUM ('monthly', 'season');
CREATE TYPE subscription_status AS ENUM ('pending_payment', 'active', 'paused', 'expired', 'canceled');

-- -----------------------------------------------------------------------------
-- 2. Пользователи, согласия, исполнители, админы и чаты MAX
-- -----------------------------------------------------------------------------

CREATE TABLE users (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    max_user_id          BIGINT NOT NULL UNIQUE,
    max_chat_id          BIGINT,
    username             VARCHAR(128),
    first_name           VARCHAR(128) NOT NULL DEFAULT '',
    last_name            VARCHAR(128),
    role                 user_role NOT NULL DEFAULT 'client',
    -- ВАЖНО (правило MAX): bot_phone из request_contact используется ТОЛЬКО внутри бота
    bot_phone            VARCHAR(32),
    -- Номер, введённый клиентом вручную с явным согласием phone_transfer для связи с бригадой
    shared_contact_phone VARCHAR(32),
    email                VARCHAR(255),
    is_blocked           BOOLEAN NOT NULL DEFAULT FALSE,
    block_reason         TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE admin_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    full_name       VARCHAR(255) NOT NULL,
    role            admin_role NOT NULL,
    linked_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE user_consents (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    consent_type      consent_type NOT NULL,
    document_version  VARCHAR(32) NOT NULL,
    source            VARCHAR(32) NOT NULL DEFAULT 'max_bot',
    accepted_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_user_consents_user_type ON user_consents (user_id, consent_type, accepted_at DESC);

CREATE TABLE executor_profiles (
    user_id                UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    status                 executor_status NOT NULL DEFAULT 'pending_verification',
    is_self_employed       BOOLEAN NOT NULL DEFAULT TRUE,
    inn                    VARCHAR(12),
    share_pct_override     NUMERIC(5, 2) CHECK (share_pct_override IS NULL OR (share_pct_override >= 10 AND share_pct_override <= 95)),
    max_active_orders      SMALLINT NOT NULL DEFAULT 1 CHECK (max_active_orders >= 1 AND max_active_orders <= 10),
    rating                 NUMERIC(3, 2) NOT NULL DEFAULT 5.00 CHECK (rating >= 1.00 AND rating <= 5.00),
    ratings_count          INTEGER NOT NULL DEFAULT 0,
    completed_orders_count INTEGER NOT NULL DEFAULT 0,
    canceled_orders_count  INTEGER NOT NULL DEFAULT 0,
    has_snowblower         BOOLEAN NOT NULL DEFAULT FALSE,
    has_miniloader         BOOLEAN NOT NULL DEFAULT FALSE,
    has_tractor            BOOLEAN NOT NULL DEFAULT FALSE,
    roof_certified         BOOLEAN NOT NULL DEFAULT FALSE,
    insurance_policy_no    VARCHAR(128),
    insurance_valid_until  DATE,
    notes                  TEXT,
    verified_by            UUID REFERENCES admin_accounts(id) ON DELETE SET NULL,
    verified_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_executor_profiles_active_rating
    ON executor_profiles (status, rating DESC)
    WHERE status = 'active';

-- Замена удалённому GET /chats в MAX Bot API (с июня 2026)
CREATE TABLE bot_chats (
    chat_id       BIGINT PRIMARY KEY,
    title         VARCHAR(255),
    chat_purpose  chat_purpose NOT NULL DEFAULT 'other',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    bot_is_admin  BOOLEAN NOT NULL DEFAULT FALSE,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- 3. Справочники: геозоны и версионируемые тарифы
-- -----------------------------------------------------------------------------

CREATE TABLE geo_zones (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code             VARCHAR(64) NOT NULL UNIQUE,
    name             VARCHAR(128) NOT NULL,
    tier             geo_zone_tier NOT NULL DEFAULT 'core',
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    dispatch_fee     NUMERIC(12, 2) NOT NULL DEFAULT 0.00 CHECK (dispatch_fee >= 0),
    base_lat         DOUBLE PRECISION NOT NULL,
    base_lon         DOUBLE PRECISION NOT NULL,
    free_radius_km   NUMERIC(6, 2) NOT NULL DEFAULT 5.00 CHECK (free_radius_km >= 0),
    per_km_rate      NUMERIC(12, 2) NOT NULL DEFAULT 100.00 CHECK (per_km_rate >= 0),
    polygon_geojson  JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE tariff_configs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version     INTEGER NOT NULL UNIQUE,
    is_active   BOOLEAN NOT NULL DEFAULT FALSE,
    config_json JSONB NOT NULL,
    comment     TEXT,
    updated_by  UUID REFERENCES admin_accounts(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Гарантия ровно одного активного тарифа
CREATE UNIQUE INDEX uq_tariff_configs_single_active
    ON tariff_configs (is_active)
    WHERE is_active = TRUE;

-- -----------------------------------------------------------------------------
-- 4. Подписки (Этап 3) и групповые заказы соседей (Этап 3)
-- -----------------------------------------------------------------------------

CREATE TABLE subscriptions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id             UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    geo_zone_id           UUID NOT NULL REFERENCES geo_zones(id) ON DELETE RESTRICT,
    plan_type             subscription_plan_type NOT NULL,
    status                subscription_status NOT NULL DEFAULT 'pending_payment',
    address_text          TEXT NOT NULL,
    public_area_hint      VARCHAR(255) NOT NULL,
    lat                   DOUBLE PRECISION,
    lon                   DOUBLE PRECISION,
    area_m2               NUMERIC(10, 2) NOT NULL CHECK (area_m2 > 0),
    cleaning_method       cleaning_method NOT NULL DEFAULT 'manual',
    access_constraint     VARCHAR(32) NOT NULL DEFAULT 'standard',
    snow_trigger_cm       NUMERIC(5, 2) NOT NULL DEFAULT 5.00 CHECK (snow_trigger_cm >= 1),
    preferred_time_window VARCHAR(64) NOT NULL DEFAULT '07:00-10:00',
    auto_confirm_hours    SMALLINT NOT NULL DEFAULT 4 CHECK (auto_confirm_hours >= 1),
    price_paid            NUMERIC(12, 2) NOT NULL CHECK (price_paid >= 0),
    starts_at             TIMESTAMPTZ NOT NULL,
    ends_at               TIMESTAMPTZ NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (ends_at > starts_at)
);

CREATE INDEX idx_subscriptions_active_zone ON subscriptions (geo_zone_id, status) WHERE status = 'active';

CREATE TABLE order_groups (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_date  DATE NOT NULL,
    geo_zone_id   UUID NOT NULL REFERENCES geo_zones(id) ON DELETE RESTRICT,
    cluster_key   VARCHAR(255) NOT NULL,
    orders_count  INTEGER NOT NULL DEFAULT 0 CHECK (orders_count >= 0),
    discount_pct  NUMERIC(5, 2) NOT NULL DEFAULT 0.00 CHECK (discount_pct >= 0 AND discount_pct <= 50),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (service_date, geo_zone_id, cluster_key)
);

-- -----------------------------------------------------------------------------
-- 5. Заказы, назначения, медиа, история, перерасчёт, споры, прокси-чат
-- -----------------------------------------------------------------------------

CREATE TABLE orders (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    public_number           BIGSERIAL NOT NULL UNIQUE,
    client_id               UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    service_type            service_type NOT NULL,
    status                  order_status NOT NULL DEFAULT 'draft',
    payment_status          order_payment_status NOT NULL DEFAULT 'unpaid',
    geo_zone_id             UUID REFERENCES geo_zones(id) ON DELETE RESTRICT,
    subscription_id         UUID REFERENCES subscriptions(id) ON DELETE SET NULL,
    order_group_id          UUID REFERENCES order_groups(id) ON DELETE SET NULL,

    -- Адрес и геоданные (address_text скрыт в общем чате, публикуется только public_area_hint)
    address_text            TEXT NOT NULL,
    public_area_hint        VARCHAR(255) NOT NULL,
    cluster_key             VARCHAR(255),
    lat                     DOUBLE PRECISION,
    lon                     DOUBLE PRECISION,
    distance_from_base_km   NUMERIC(7, 2),

    -- Параметры услуги
    params_json             JSONB NOT NULL DEFAULT '{}'::jsonb,
    cleaning_method         cleaning_method,
    urgency                 urgency_level NOT NULL DEFAULT 'today',
    required_executors      SMALLINT NOT NULL DEFAULT 1 CHECK (required_executors >= 1 AND required_executors <= 10),
    scheduled_for           TIMESTAMPTZ,

    -- Ценообразование (снимок тарифа фиксируется при создании заказа)
    tariff_config_id        UUID NOT NULL REFERENCES tariff_configs(id) ON DELETE RESTRICT,
    pricing_snapshot        JSONB NOT NULL,
    estimated_total         NUMERIC(12, 2) NOT NULL CHECK (estimated_total >= 0),
    final_total             NUMERIC(12, 2) NOT NULL CHECK (final_total >= 0),
    advance_amount          NUMERIC(12, 2) NOT NULL DEFAULT 0.00 CHECK (advance_amount >= 0),
    paid_amount             NUMERIC(12, 2) NOT NULL DEFAULT 0.00 CHECK (paid_amount >= 0),
    group_discount_amount   NUMERIC(12, 2) NOT NULL DEFAULT 0.00 CHECK (group_discount_amount >= 0),
    executor_bonus_amount   NUMERIC(12, 2) NOT NULL DEFAULT 0.00 CHECK (executor_bonus_amount >= 0),

    -- Диспетчеризация в MAX
    dispatch_wave           SMALLINT NOT NULL DEFAULT 0,
    dispatch_chat_id        BIGINT,
    dispatch_message_id     VARCHAR(255),

    -- Связь и обратная связь
    contact_mode            contact_mode NOT NULL DEFAULT 'bot_proxy',
    client_comment          TEXT,
    client_rating           SMALLINT CHECK (client_rating IS NULL OR (client_rating >= 1 AND client_rating <= 5)),
    client_review           TEXT,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX idx_orders_status_created ON orders (status, created_at DESC);
CREATE INDEX idx_orders_client_created ON orders (client_id, created_at DESC);
CREATE INDEX idx_orders_geo_zone_date ON orders (geo_zone_id, scheduled_for);
CREATE INDEX idx_orders_group ON orders (order_group_id) WHERE order_group_id IS NOT NULL;

CREATE TABLE order_assignments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id            UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    executor_id         UUID NOT NULL REFERENCES executor_profiles(user_id) ON DELETE RESTRICT,
    role_in_order       assignment_role NOT NULL DEFAULT 'primary',
    status              assignment_status NOT NULL DEFAULT 'assigned',
    executor_share_pct  NUMERIC(5, 2) NOT NULL CHECK (executor_share_pct >= 0 AND executor_share_pct <= 100),
    accepted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    en_route_at         TIMESTAMPTZ,
    arrived_at          TIMESTAMPTZ,
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    released_at         TIMESTAMPTZ,
    release_reason      TEXT
);

-- Один и тот же исполнитель не может дважды активно занять один и тот же заказ
CREATE UNIQUE INDEX uq_order_assignments_active
    ON order_assignments (order_id, executor_id)
    WHERE status IN ('assigned', 'en_route', 'arrived', 'in_progress', 'completed');

CREATE INDEX idx_order_assignments_executor_active
    ON order_assignments (executor_id, status)
    WHERE status IN ('assigned', 'en_route', 'arrived', 'in_progress');

CREATE TABLE order_media (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id      UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    uploaded_by   UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    stage         media_stage NOT NULL,
    storage_key   TEXT NOT NULL,
    max_photo_id  BIGINT,
    max_token     TEXT,
    mime_type     VARCHAR(64) NOT NULL DEFAULT 'image/jpeg',
    size_bytes    BIGINT NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    sha256        VARCHAR(64),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_order_media_order_stage ON order_media (order_id, stage);

CREATE TABLE price_adjustments (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id             UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    proposed_by          UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    old_total            NUMERIC(12, 2) NOT NULL CHECK (old_total >= 0),
    new_total            NUMERIC(12, 2) NOT NULL CHECK (new_total >= 0),
    delta_pct            NUMERIC(6, 2) NOT NULL,
    reason               TEXT NOT NULL,
    status               price_adjustment_status NOT NULL DEFAULT 'pending',
    expires_at           TIMESTAMPTZ NOT NULL,
    client_responded_at  TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Одновременно не более одной pending-корректировки на заказ
CREATE UNIQUE INDEX uq_price_adjustments_pending
    ON price_adjustments (order_id)
    WHERE status = 'pending';

CREATE TABLE disputes (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id         UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    opened_by        UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_admin   UUID REFERENCES admin_accounts(id) ON DELETE SET NULL,
    reason           TEXT NOT NULL,
    status           dispute_status NOT NULL DEFAULT 'open',
    resolution_note  TEXT,
    discount_amount  NUMERIC(12, 2) NOT NULL DEFAULT 0.00 CHECK (discount_amount >= 0),
    refund_amount    NUMERIC(12, 2) NOT NULL DEFAULT 0.00 CHECK (refund_amount >= 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at      TIMESTAMPTZ
);

CREATE INDEX idx_disputes_status ON disputes (status, created_at DESC);

CREATE TABLE order_status_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    from_status     order_status,
    to_status       order_status NOT NULL,
    actor_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
    actor_admin_id  UUID REFERENCES admin_accounts(id) ON DELETE SET NULL,
    reason          TEXT,
    meta_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_order_status_history_order ON order_status_history (order_id, created_at ASC);

CREATE TABLE order_messages (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id           UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    sender_user_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    recipient_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    text               TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_order_messages_order ON order_messages (order_id, created_at ASC);

-- -----------------------------------------------------------------------------
-- 6. Платежи (ЮKassa / Mock), возвраты, начисления и выплаты исполнителям
-- -----------------------------------------------------------------------------

CREATE TABLE payments (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id             UUID REFERENCES orders(id) ON DELETE RESTRICT,
    subscription_id      UUID REFERENCES subscriptions(id) ON DELETE RESTRICT,
    purpose              payment_purpose NOT NULL,
    provider             payment_provider_type NOT NULL,
    provider_payment_id  VARCHAR(128) UNIQUE,
    idempotency_key      UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    amount               NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency             CHAR(3) NOT NULL DEFAULT 'RUB',
    status               payment_status NOT NULL DEFAULT 'pending',
    confirmation_url     TEXT,
    receipt_status       VARCHAR(32),
    expires_at           TIMESTAMPTZ,
    paid_at              TIMESTAMPTZ,
    raw_response         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((order_id IS NOT NULL) OR (subscription_id IS NOT NULL))
);

CREATE INDEX idx_payments_order ON payments (order_id, purpose);
CREATE INDEX idx_payments_pending_reconcile ON payments (status, created_at) WHERE status = 'pending';

CREATE TABLE refunds (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id          UUID NOT NULL REFERENCES payments(id) ON DELETE RESTRICT,
    order_id            UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    provider_refund_id  VARCHAR(128) UNIQUE,
    idempotency_key     UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    amount              NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    reason              TEXT NOT NULL,
    status              refund_status NOT NULL DEFAULT 'pending',
    initiated_by        UUID REFERENCES admin_accounts(id) ON DELETE SET NULL,
    raw_response        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    succeeded_at        TIMESTAMPTZ
);

CREATE TABLE payout_batches (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    executor_id          UUID NOT NULL REFERENCES executor_profiles(user_id) ON DELETE RESTRICT,
    total_amount         NUMERIC(12, 2) NOT NULL CHECK (total_amount > 0),
    status               payout_batch_status NOT NULL DEFAULT 'draft',
    payment_method_note  VARCHAR(255),
    npd_receipt_id       VARCHAR(128),
    npd_receipt_url      TEXT,
    paid_by              UUID REFERENCES admin_accounts(id) ON DELETE SET NULL,
    paid_at              TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE payout_ledger_entries (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    executor_id      UUID NOT NULL REFERENCES executor_profiles(user_id) ON DELETE RESTRICT,
    order_id         UUID REFERENCES orders(id) ON DELETE SET NULL,
    entry_type       ledger_entry_type NOT NULL,
    amount           NUMERIC(12, 2) NOT NULL, -- положительное начисление или отрицательный штраф
    status           ledger_entry_status NOT NULL DEFAULT 'accrued',
    payout_batch_id  UUID REFERENCES payout_batches(id) ON DELETE SET NULL,
    comment          TEXT,
    created_by_admin UUID REFERENCES admin_accounts(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_payout_ledger_executor_status ON payout_ledger_entries (executor_id, status);

-- -----------------------------------------------------------------------------
-- 7. Идемпотентность вебхуков, аудит админки и симулятор MAX
-- -----------------------------------------------------------------------------

CREATE TABLE processed_webhook_events (
    id           BIGSERIAL PRIMARY KEY,
    source       VARCHAR(32) NOT NULL, -- 'max' | 'yookassa'
    event_key    VARCHAR(255) NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, event_key)
);

CREATE TABLE audit_logs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id     UUID REFERENCES admin_accounts(id) ON DELETE SET NULL,
    action       VARCHAR(128) NOT NULL,
    entity_type  VARCHAR(64) NOT NULL,
    entity_id    VARCHAR(128) NOT NULL,
    diff_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address   INET,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_entity ON audit_logs (entity_type, entity_id, created_at DESC);

-- Таблица используется только при ENABLE_SIMULATOR=true (dev/stage)
CREATE TABLE simulator_messages (
    id                   BIGSERIAL PRIMARY KEY,
    chat_id              BIGINT NOT NULL,
    chat_type            VARCHAR(16) NOT NULL CHECK (chat_type IN ('dialog', 'group')),
    sender_role          VARCHAR(32) NOT NULL, -- 'bot' | 'client' | 'executor' | 'foreman'
    sender_max_user_id   BIGINT NOT NULL,
    max_message_id       VARCHAR(128) NOT NULL UNIQUE,
    text                 TEXT NOT NULL DEFAULT '',
    attachments_json     JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_edited            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_simulator_messages_chat ON simulator_messages (chat_id, id ASC);

-- -----------------------------------------------------------------------------
-- 8. Стартовые справочные данные (Seed для Этапа 0 / 1)
-- -----------------------------------------------------------------------------

INSERT INTO geo_zones (code, name, tier, is_active, dispatch_fee, base_lat, base_lon, free_radius_km, per_km_rate)
VALUES
    ('malakhovka', 'Малаховка', 'core',     TRUE,  500.00, 55.6435, 38.0068, 5.00, 100.00),
    ('tomilino',   'Томилино',  'core',     TRUE,  500.00, 55.6435, 38.0068, 5.00, 100.00),
    ('kraskovo',   'Красково',  'extended', TRUE,  600.00, 55.6435, 38.0068, 5.00, 100.00),
    ('bykovo',     'Быково',    'extended', TRUE,  600.00, 55.6435, 38.0068, 5.00, 100.00),
    ('lytkarino',  'Лыткарино', 'extended', TRUE,  800.00, 55.6435, 38.0068, 5.00, 100.00),
    ('oktyabrsky', 'Октябрьский', 'planned', FALSE, 800.00, 55.6435, 38.0068, 5.00, 100.00),
    ('lyubertsy',  'Люберцы',     'planned', FALSE, 800.00, 55.6435, 38.0068, 5.00, 100.00)
ON CONFLICT (code) DO NOTHING;

INSERT INTO tariff_configs (version, is_active, comment, config_json)
VALUES (
    1,
    TRUE,
    'Прайс МО 2026 с учётом аренды техники на субподряде',
    '{
      "rounding_step_rub": 50,
      "night_window_msk": {"start_hour": 22, "end_hour": 7},
      "min_order_total_rub": 2000,
      "min_advance_rub": 700,
      "subcontract_advance_pct": 50,
      "advance_pct_by_service": {
        "snow_yard": 30,
        "snow_road": 50,
        "car_rescue": 50,
        "yard_help": 30,
        "movers": 30,
        "roof_cleaning": 40,
        "reagents": 50
      },
      "executor_share_pct_by_service": {
        "snow_yard": 65,
        "snow_road": 65,
        "car_rescue": 65,
        "yard_help": 70,
        "movers": 70,
        "roof_cleaning": 60,
        "reagents": 65
      },
      "dispatch_compensation_pct_to_executor": 100,
      "tax_and_acquiring_pct": 7.9,
      "equipment_rental_estimates": {
        "snowblower_rental_rub": 2200,
        "tractor_subcontract_share_pct": 70,
        "snow_removal_subcontract_share_pct": 72,
        "car_rescue_subcontract_share_pct": 65,
        "tool_rental_rub": 1200
      },
      "snow": {
        "depth_coeff": {"up_to_10": 1.0, "from_10_to_30": 1.5, "over_30": 2.0},
        "depth_avg_m": {"up_to_10": 0.08, "from_10_to_30": 0.20, "over_30": 0.40},
        "urgency_coeff": {"today": 1.0, "within_3h": 1.3, "now_or_night": 1.5},
        "methods": {
          "manual": {"rate_per_m2": 40, "min_work_price": 2500},
          "snowblower_or_miniloader": {"rate_per_m2": 42, "min_work_price": 5500},
          "tractor": {"rate_per_m2": 30, "min_work_price": 14500}
        },
        "auto_select_thresholds_m2": {"manual_max": 100, "small_tech_max": 350},
        "snow_removal": {"rate_per_m3": 850, "min_removal_price": 13500}
      },
      "car_rescue": {
        "base_price": 3500,
        "urgency_surcharge": 500,
        "night_surcharge": 1000,
        "vehicle_surcharge": {"sedan": 0, "suv": 700, "commercial": 2000},
        "free_radius_km": 5,
        "per_km_rate": 120
      },
      "hourly": {
        "min_hours": 2,
        "yard_help_rate_per_hour": 700,
        "movers_rate_per_hour": 750,
        "floor_no_lift_surcharge_per_worker": 200,
        "equipment_fee": 2000
      },
      "roof_and_reagents": {
        "roof_rate_per_m2": 90,
        "roof_min_price": 6000,
        "reagents_rate_per_m2": 20,
        "reagents_min_price": 1000
      },
      "group_discount_tiers": [
        {"min_orders": 2, "discount_pct": 10},
        {"min_orders": 3, "discount_pct": 15}
      ],
      "dispatch_waves": {
        "wave1_top_rating_min": 4.5,
        "wave1_duration_sec": 180,
        "wave2_duration_sec": 300,
        "wave3_bonus_rub": 300,
        "wave3_duration_sec": 300,
        "escalate_foreman_after_sec": 600
      },
      "price_adjustment_threshold_pct": 20
    }'::jsonb
)
ON CONFLICT (version) DO NOTHING;
