-- SignalProof :: 0004_candidates
-- The pre-signal watchlist. Makes WATCHING / SETUP_FORMING / NO TRADE first-class,
-- recorded, and visible, instead of silent engine no-ops.

CREATE TABLE IF NOT EXISTS candidates (
    id            bigserial PRIMARY KEY,
    symbol        text NOT NULL,
    timeframe     text NOT NULL,
    direction     text NOT NULL CHECK (direction IN ('LONG','SHORT','NONE')),
    state         text NOT NULL CHECK (state IN ('WATCHING','SETUP_FORMING','PROMOTED','DROPPED')),
    signal_class  text NOT NULL,
    strength      integer NOT NULL,
    conflict      numeric(6,4) NOT NULL DEFAULT 0,
    blockers      jsonb NOT NULL DEFAULT '[]'::jsonb,   -- why it is not (yet) a signal
    signal_id     uuid REFERENCES signals(signal_id),
    first_seen    timestamptz NOT NULL DEFAULT now(),
    last_seen     timestamptz NOT NULL DEFAULT now(),
    is_simulated  boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS candidates_open_uidx
    ON candidates (symbol, timeframe) WHERE state IN ('WATCHING','SETUP_FORMING');
CREATE INDEX IF NOT EXISTS candidates_seen_idx ON candidates (last_seen DESC);

-- Append-only transition log.
CREATE TABLE IF NOT EXISTS candidate_events (
    id            bigserial PRIMARY KEY,
    candidate_id  bigint NOT NULL REFERENCES candidates(id),
    ts            timestamptz NOT NULL DEFAULT now(),
    from_state    text,
    to_state      text NOT NULL,
    signal_class  text NOT NULL,
    strength      integer NOT NULL,
    reasons       jsonb NOT NULL DEFAULT '[]'::jsonb
);
DROP TRIGGER IF EXISTS candidate_events_no_update ON candidate_events;
CREATE TRIGGER candidate_events_no_update BEFORE UPDATE OR DELETE ON candidate_events
    FOR EACH ROW EXECUTE FUNCTION signals_immutable();

GRANT SELECT, INSERT, UPDATE ON candidates TO signalproof_app;
GRANT SELECT, INSERT ON candidate_events TO signalproof_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO signalproof_app;
