-- SignalProof :: 0002_accounts
-- Users, tiering, alerts, calibration fits, and the gate that keeps
-- smart-money data context-only until it earns promotion.

CREATE TABLE IF NOT EXISTS users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email          citext_or_text_placeholder text,   -- replaced below
    created_at     timestamptz NOT NULL DEFAULT now()
);
DROP TABLE IF EXISTS users;

CREATE TABLE IF NOT EXISTS users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email          text NOT NULL,
    email_lower    text GENERATED ALWAYS AS (lower(email)) STORED,
    password_hash  text NOT NULL,
    tier           text NOT NULL DEFAULT 'free' CHECK (tier IN ('free','pro')),
    tz             text NOT NULL DEFAULT 'UTC',
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_uidx ON users (email_lower);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti        uuid PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    issued_at  timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id            uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    provider           text NOT NULL,
    provider_customer  text,
    provider_sub_id    text,
    status             text NOT NULL DEFAULT 'inactive'
                       CHECK (status IN ('inactive','trialing','active','past_due','canceled')),
    price_usd          numeric(8,2) NOT NULL DEFAULT 10.00,
    current_period_end timestamptz,
    cancel_at_period_end boolean NOT NULL DEFAULT false,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS billing_events (
    id           bigserial PRIMARY KEY,
    provider     text NOT NULL,
    event_id     text NOT NULL,
    event_type   text NOT NULL,
    user_id      uuid REFERENCES users(id) ON DELETE SET NULL,
    payload      jsonb NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, event_id)          -- webhook idempotency
);

CREATE TABLE IF NOT EXISTS alert_prefs (
    id         bigserial PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel    text NOT NULL CHECK (channel IN ('telegram','webpush','email','discord')),
    symbol     text NOT NULL CHECK (symbol IN ('BTCUSDT','ETHUSDT','SOLUSDT','ALL')),
    timeframe  text NOT NULL,
    direction  text NOT NULL CHECK (direction IN ('LONG','SHORT','ALL')),
    min_strength integer NOT NULL DEFAULT 0,
    enabled    boolean NOT NULL DEFAULT true,
    UNIQUE (user_id, channel, symbol, timeframe, direction)
);

CREATE TABLE IF NOT EXISTS alert_channels (
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel     text NOT NULL,
    address     text NOT NULL,          -- chat id / push endpoint / email
    verified    boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, channel)
);

-- Dedup ledger: one alert per (signal, user, channel), enforced by the PK.
CREATE TABLE IF NOT EXISTS alert_deliveries (
    signal_id   uuid NOT NULL REFERENCES signals(signal_id),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel     text NOT NULL,
    queued_at   timestamptz NOT NULL DEFAULT now(),
    sent_at     timestamptz,
    status      text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','sent','failed','suppressed')),
    attempts    smallint NOT NULL DEFAULT 0,
    error       text,
    PRIMARY KEY (signal_id, user_id, channel)
);

-- Fitted Platt / isotonic mappings, one row per (strategy_version, bucket key).
CREATE TABLE IF NOT EXISTS calibration_fits (
    id               bigserial PRIMARY KEY,
    strategy_version text NOT NULL,
    method           text NOT NULL CHECK (method IN ('platt','isotonic')),
    bucket_key       text NOT NULL,      -- e.g. "BTCUSDT|1h|trend_bull"
    params           jsonb NOT NULL,
    sample_size      integer NOT NULL,
    brier            numeric(8,6),
    log_loss         numeric(8,6),
    ece              numeric(8,6),
    fitted_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (strategy_version, method, bucket_key)
);

-- The promotion gate for on-chain smart money. The engine reads this table at
-- startup; with no passing row, smart-money reason codes are emitted with
-- contribution = 0 and role = "context_only". Period-A selection must predict
-- period-B returns (research §5).
CREATE TABLE IF NOT EXISTS smart_money_validation (
    id                bigserial PRIMARY KEY,
    cohort_id         text NOT NULL,
    chain             text NOT NULL,
    symbol            text NOT NULL,
    select_start      date NOT NULL,
    select_end        date NOT NULL,
    validate_start    date NOT NULL,
    validate_end      date NOT NULL,
    wallets_selected  integer NOT NULL,
    oos_ic            numeric(8,5),
    oos_p_value       numeric(8,6),
    deflated_sharpe   numeric(8,5),
    passed            boolean NOT NULL DEFAULT false,
    notes             text NOT NULL DEFAULT '',
    evaluated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS smart_wallets (
    address        text NOT NULL,
    chain          text NOT NULL,
    cohort_id      text NOT NULL,
    score          numeric(6,4) NOT NULL,
    n_trades       integer NOT NULL,
    win_rate       numeric(6,4),
    mean_excess    numeric(10,6),
    t_stat         numeric(10,4),
    q_value        numeric(10,6),        -- Benjamini-Hochberg adjusted
    label          text,
    excluded       boolean NOT NULL DEFAULT false,
    exclusion_reason text,
    scored_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chain, address, cohort_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    actor       text NOT NULL,
    action      text NOT NULL,
    entity      text,
    entity_id   text,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb
);
