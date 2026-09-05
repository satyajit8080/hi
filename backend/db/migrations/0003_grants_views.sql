-- SignalProof :: 0003_grants_views
-- Least-privilege role for the application + read-model views.
--
-- The app connects as signalproof_app, which physically cannot UPDATE or DELETE
-- a published signal. Migrations run as the owner. This is the grant layer of
-- the immutability design; the trigger in 0001 is the belt to this suspenders.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signalproof_app') THEN
        CREATE ROLE signalproof_app NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO signalproof_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO signalproof_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO signalproof_app;

-- ...then take the dangerous verbs back on the ledger tables.
REVOKE UPDATE, DELETE, TRUNCATE ON signals       FROM signalproof_app;
REVOKE UPDATE, DELETE, TRUNCATE ON signal_events FROM signalproof_app;
REVOKE UPDATE, DELETE, TRUNCATE ON anchors       FROM signalproof_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO signalproof_app;

-- ────────────────────────────── read models ─────────────────────────────────

CREATE OR REPLACE VIEW v_signals_full AS
SELECT s.*,
       st.status,
       st.outcome,
       st.tp_hits,
       st.mfe_pct,
       st.mae_pct,
       st.close_price,
       st.closed_at,
       st.close_reason,
       st.pnl_pct,
       st.r_multiple
FROM signals s
LEFT JOIN signal_state st ON st.signal_id = s.signal_id;

-- Closed signals only: the honest denominator for every published statistic.
CREATE OR REPLACE VIEW v_closed_signals AS
SELECT *
FROM v_signals_full
WHERE outcome IS NOT NULL;

CREATE OR REPLACE VIEW v_performance_daily AS
SELECT date_trunc('day', closed_at)::date AS day,
       symbol,
       timeframe,
       count(*)                                            AS total,
       count(*) FILTER (WHERE outcome = 'WIN')             AS wins,
       count(*) FILTER (WHERE outcome = 'LOSS')            AS losses,
       count(*) FILTER (WHERE outcome = 'EXPIRED')         AS expired,
       count(*) FILTER (WHERE outcome = 'CANCELLED')       AS cancelled,
       avg(pnl_pct)                                        AS avg_pnl_pct,
       sum(pnl_pct) FILTER (WHERE pnl_pct > 0)             AS gross_profit,
       abs(sum(pnl_pct) FILTER (WHERE pnl_pct < 0))        AS gross_loss
FROM v_closed_signals
GROUP BY 1, 2, 3;

-- Chain integrity at a glance: any row where prev_hash does not match the
-- previous row's row_hash is a broken link and is exposed by /v1/verify/chain.
CREATE OR REPLACE VIEW v_chain_links AS
SELECT s.seq,
       s.signal_id,
       s.published_at,
       s.prev_hash,
       s.row_hash,
       lag(s.row_hash) OVER (ORDER BY s.seq) AS expected_prev_hash
FROM signals s;
