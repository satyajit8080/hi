-- SignalProof :: 0001_init
-- Time-series market data, immutable signal ledger, append-only lifecycle log.
--
-- Immutability model (research doc §M / §13):
--   * `signals` is APPEND-ONLY. The application role has INSERT + SELECT only;
--     UPDATE and DELETE are revoked and additionally blocked by a trigger, so a
--     compromised app cannot rewrite history even if grants drift.
--   * Mutable state (current lifecycle status, MFE/MAE, outcome) lives in
--     `signal_state`, which is fully reconstructible by replaying
--     `signal_events`. Losing signal_state loses nothing auditable.
--   * Each signal carries row_hash = sha256(canonical_payload || prev_hash),
--     forming a tamper-evident chain per the Haber-Stornetta construction.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ───────────────────────────── market data ──────────────────────────────────

CREATE TABLE IF NOT EXISTS ohlcv (
    venue       text        NOT NULL,
    symbol      text        NOT NULL,
    timeframe   text        NOT NULL,
    ts          timestamptz NOT NULL,
    open        numeric(20,8) NOT NULL,
    high        numeric(20,8) NOT NULL,
    low         numeric(20,8) NOT NULL,
    close       numeric(20,8) NOT NULL,
    volume      numeric(28,8) NOT NULL,
    trades      integer,
    is_simulated boolean NOT NULL DEFAULT false,
    PRIMARY KEY (venue, symbol, timeframe, ts)
);
SELECT create_hypertable('ohlcv', 'ts', if_not_exists => TRUE, chunk_time_interval => interval '7 days');

-- Derived microstructure metrics only. We deliberately do NOT store full L2
-- tick history: research §1 concluded derived-metrics-only is the right call
-- for a single-operator deployment (full L2 is ~1-5 GB/day/symbol).
CREATE TABLE IF NOT EXISTS microstructure (
    venue           text        NOT NULL,
    symbol          text        NOT NULL,
    ts              timestamptz NOT NULL,
    best_bid        numeric(20,8),
    best_ask        numeric(20,8),
    mid             numeric(20,8),
    micro_price     numeric(20,8),
    spread_abs      numeric(20,8),
    spread_rel      numeric(12,8),
    spread_z        numeric(10,4),
    obi_5           numeric(10,6),
    obi_20          numeric(10,6),
    obi_persistent  numeric(10,6),   -- time-weighted, spoof-resistant (§3)
    ofi             numeric(20,8),
    ofi_z           numeric(10,4),
    cvd_spot        numeric(28,8),
    cvd_perp        numeric(28,8),
    trade_imbalance numeric(10,6),
    large_print_z   numeric(10,4),
    realized_vol    numeric(14,8),
    book_synced     boolean NOT NULL DEFAULT true,
    is_simulated    boolean NOT NULL DEFAULT false,
    PRIMARY KEY (venue, symbol, ts)
);
SELECT create_hypertable('microstructure', 'ts', if_not_exists => TRUE, chunk_time_interval => interval '2 days');

CREATE TABLE IF NOT EXISTS derivatives (
    symbol            text        NOT NULL,
    ts                timestamptz NOT NULL,
    source            text        NOT NULL,
    funding_rate      numeric(14,10),
    next_funding_ts   timestamptz,
    open_interest     numeric(28,8),
    oi_change_pct     numeric(12,6),
    liq_long_usd      numeric(20,4),
    liq_short_usd     numeric(20,4),
    is_simulated      boolean NOT NULL DEFAULT false,
    PRIMARY KEY (symbol, source, ts)
);
SELECT create_hypertable('derivatives', 'ts', if_not_exists => TRUE, chunk_time_interval => interval '30 days');

CREATE TABLE IF NOT EXISTS liquidations (
    id           bigserial,
    symbol       text        NOT NULL,
    venue        text        NOT NULL,
    ts           timestamptz NOT NULL,
    side         text        NOT NULL CHECK (side IN ('long','short')),
    price        numeric(20,8) NOT NULL,
    qty          numeric(28,8) NOT NULL,
    notional_usd numeric(20,4) NOT NULL,
    is_simulated boolean NOT NULL DEFAULT false,
    PRIMARY KEY (id, ts)
);
SELECT create_hypertable('liquidations', 'ts', if_not_exists => TRUE, chunk_time_interval => interval '7 days');

-- CONTEXT ONLY. Never joined into signal scoring unless a passing row exists in
-- smart_money_validation (see 0002).
CREATE TABLE IF NOT EXISTS smart_money_flow (
    chain        text        NOT NULL,
    symbol       text        NOT NULL,
    ts           timestamptz NOT NULL,
    source       text        NOT NULL,
    cohort_size  integer     NOT NULL,
    net_flow_usd numeric(20,4),
    accum_z      numeric(10,4),
    exchange_netflow_usd numeric(20,4),
    is_simulated boolean NOT NULL DEFAULT false,
    PRIMARY KEY (chain, symbol, source, ts)
);
SELECT create_hypertable('smart_money_flow', 'ts', if_not_exists => TRUE, chunk_time_interval => interval '30 days');

CREATE TABLE IF NOT EXISTS feed_health (
    venue         text NOT NULL,
    stream        text NOT NULL,
    ts            timestamptz NOT NULL DEFAULT now(),
    last_event_ts timestamptz,
    staleness_ms  integer,
    clock_skew_ms integer,
    book_synced   boolean,
    status        text NOT NULL CHECK (status IN ('live','degraded','stale','down')),
    detail        text,
    PRIMARY KEY (venue, stream)
);

-- ─────────────────────────── immutable signals ──────────────────────────────

CREATE TABLE IF NOT EXISTS signals (
    signal_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq              bigint GENERATED ALWAYS AS IDENTITY,
    created_at       timestamptz NOT NULL DEFAULT now(),
    published_at     timestamptz NOT NULL DEFAULT now(),
    symbol           text NOT NULL,
    direction        text NOT NULL CHECK (direction IN ('LONG','SHORT')),
    timeframe        text NOT NULL,
    regime           text NOT NULL,
    strategy_version text NOT NULL,
    strength         integer NOT NULL CHECK (strength BETWEEN 0 AND 100),
    mtf_alignment    numeric(5,4) NOT NULL,
    calibrated_winrate numeric(6,4),
    calibration_sample integer,
    calibration_ci_low  numeric(6,4),
    calibration_ci_high numeric(6,4),
    entry            numeric(20,8) NOT NULL,
    stop_loss        numeric(20,8) NOT NULL,
    tp1              numeric(20,8) NOT NULL,
    tp2              numeric(20,8) NOT NULL,
    tp3              numeric(20,8) NOT NULL,
    risk_reward      numeric(8,3) NOT NULL,
    risk_category    text NOT NULL,
    expires_at       timestamptz NOT NULL,
    invalidation     text NOT NULL,
    features         jsonb NOT NULL,   -- full feature snapshot, replayable
    reason_codes     jsonb NOT NULL,   -- drivers + context_only, w/ contributions
    risk_flags       jsonb NOT NULL,
    data_quality     jsonb NOT NULL,
    is_simulated     boolean NOT NULL DEFAULT false,
    prev_hash        text NOT NULL,
    row_hash         text NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS signals_symbol_pub_idx ON signals (symbol, published_at DESC);
CREATE INDEX IF NOT EXISTS signals_tf_pub_idx     ON signals (timeframe, published_at DESC);
CREATE INDEX IF NOT EXISTS signals_seq_idx        ON signals (seq);

CREATE OR REPLACE FUNCTION signals_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'signals is append-only: % on signal_id % refused (append a signal_events row instead)',
        TG_OP, COALESCE(OLD.signal_id::text, '?');
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS signals_no_update ON signals;
CREATE TRIGGER signals_no_update BEFORE UPDATE OR DELETE ON signals
    FOR EACH ROW EXECUTE FUNCTION signals_immutable();

-- Append-only lifecycle log. Single source of truth for signal state.
CREATE TABLE IF NOT EXISTS signal_events (
    id         bigserial PRIMARY KEY,
    signal_id  uuid NOT NULL REFERENCES signals(signal_id),
    event      text NOT NULL CHECK (event IN (
                 'WATCHING','SETUP_FORMING','SIGNAL_GENERATED','ACTIVE',
                 'TP1_HIT','TP2_HIT','TP3_HIT','STOPPED','EXPIRED','CANCELLED')),
    ts         timestamptz NOT NULL DEFAULT now(),
    price      numeric(20,8),
    reason     text,
    meta       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS signal_events_signal_idx ON signal_events (signal_id, ts);

DROP TRIGGER IF EXISTS signal_events_no_update ON signal_events;
CREATE TRIGGER signal_events_no_update BEFORE UPDATE OR DELETE ON signal_events
    FOR EACH ROW EXECUTE FUNCTION signals_immutable();

-- Derived, rebuildable projection of the event log.
CREATE TABLE IF NOT EXISTS signal_state (
    signal_id     uuid PRIMARY KEY REFERENCES signals(signal_id),
    status        text NOT NULL,
    outcome       text CHECK (outcome IN ('WIN','LOSS','EXPIRED','CANCELLED')),
    tp_hits       smallint NOT NULL DEFAULT 0,
    mfe_pct       numeric(12,6) NOT NULL DEFAULT 0,
    mae_pct       numeric(12,6) NOT NULL DEFAULT 0,
    close_price   numeric(20,8),
    closed_at     timestamptz,
    close_reason  text,
    pnl_pct       numeric(12,6),
    r_multiple    numeric(10,4),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS signal_state_status_idx ON signal_state (status);

-- Daily Merkle roots over the day's signal hashes, optionally anchored to
-- Bitcoin via OpenTimestamps (research §M, layer 4).
CREATE TABLE IF NOT EXISTS anchors (
    day            date PRIMARY KEY,
    merkle_root    text NOT NULL,
    signal_count   integer NOT NULL,
    first_seq      bigint NOT NULL,
    last_seq       bigint NOT NULL,
    chain_head     text NOT NULL,
    provider       text NOT NULL,
    proof          bytea,
    proof_status   text NOT NULL DEFAULT 'pending',
    anchored_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    version      text PRIMARY KEY,
    released_at  timestamptz NOT NULL DEFAULT now(),
    notes        text NOT NULL,
    weights      jsonb NOT NULL,
    config_hash  text NOT NULL
);
