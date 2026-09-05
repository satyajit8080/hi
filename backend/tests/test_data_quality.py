"""Data reliability: dedup, skew, circuit breakers, divergence, failover, unclosed bars."""
from __future__ import annotations

import time

import pytest

from app.config import settings
from app.market.adapters.base import MarketEvent
from app.market.hub import MarketHub, VenueCircuit
from app.features.candles import drop_unclosed

def _now() -> int:
    return int(time.time() * 1000)


class _Now:
    """Fresh wall-clock ms on every use, so a long suite run cannot make fixtures look stale."""
    def __sub__(self, other): return _now() - other
    def __add__(self, other): return _now() + other
    def __int__(self): return _now()
    def __index__(self): return _now()


NOW = _Now()


def hub(venues=("binance", "coinbase")) -> MarketHub:
    h = MarketHub(["BTCUSDT"], list(venues), depth=10)
    for v in venues:
        h.apply(MarketEvent("heartbeat", v, "*", _now(), {"state": "connected"}))
        h.apply(MarketEvent("book_snapshot", v, "BTCUSDT", _now(), {
            "last_update_id": 100,
            "bids": [(64000.0 - i, 1.0) for i in range(10)],
            "asks": [(64001.0 + i, 1.0) for i in range(10)],
            "top_only": False,
        }))
    return h


def trade(venue, tid, price=64000.5, qty=0.1, ts=None):
    return MarketEvent("trade", venue, "BTCUSDT", ts or _now(),
                       {"price": price, "qty": qty, "is_buyer_maker": False, "trade_id": tid})


# ── duplicate detection ─────────────────────────────────────────────────────

def test_duplicate_trades_are_dropped_and_counted():
    h = hub()
    st = h.state[("binance", "BTCUSDT")]
    for _ in range(3):
        h.apply(trade("binance", 555))
    assert len(st.trades) == 1
    assert st.duplicates == 2
    assert st.cvd_spot.value == pytest.approx(0.1)     # counted once, not three times


def test_distinct_trades_all_count():
    h = hub()
    for i in range(5):
        h.apply(trade("binance", 1000 + i))
    assert len(h.state[("binance", "BTCUSDT")].trades) == 5


# ── timestamp validation ────────────────────────────────────────────────────

def test_clock_skew_is_measured_and_flags_health():
    h = hub()
    h.apply(trade("binance", 1, ts=NOW - 60_000))   # a minute behind our clock
    st = h.state[("binance", "BTCUSDT")]
    assert st.clock_skew_ms() >= 59_000
    health = h.health()
    assert health["clock_skew_ms"] >= 59_000
    assert any("clock_skew" in f for f in health["flags"])
    assert health["degraded"] is True


def test_simulated_events_do_not_contribute_skew():
    h = hub()
    ev = trade("binance", 1, ts=NOW - 60_000)
    ev.is_simulated = True
    h.apply(ev)
    assert h.state[("binance", "BTCUSDT")].clock_skew_ms() == 0


# ── circuit breaker ─────────────────────────────────────────────────────────

def test_circuit_opens_after_repeated_failures_and_cools_down():
    c = VenueCircuit(threshold=3, window_s=60, cooldown_s=120)
    t0 = 1_000_000.0
    assert c.record_failure(t0) is False
    assert c.record_failure(t0 + 1) is False
    assert c.record_failure(t0 + 2) is True          # third failure trips it
    assert c.is_open(t0 + 3) is True
    assert c.is_open(t0 + 200) is False              # cooled down
    assert c.trips == 1


def test_failures_outside_the_window_do_not_count():
    c = VenueCircuit(threshold=3, window_s=10, cooldown_s=60)
    c.record_failure(0.0); c.record_failure(1.0)
    assert c.record_failure(100.0) is False          # earlier two have aged out


def test_open_circuit_marks_venue_down_in_health():
    h = hub()
    for _ in range(5):
        h.record_failure("coinbase")
    health = h.health()
    assert health["venues"]["coinbase"]["connected"] is False
    assert health["venues"]["coinbase"].get("circuit_open") is True
    assert "coinbase:circuit_open" in health["flags"]


# ── cross-venue validation & failover ───────────────────────────────────────

def test_price_divergence_across_venues_is_flagged():
    h = hub()
    h.apply(MarketEvent("book_snapshot", "coinbase", "BTCUSDT", _now(), {
        "last_update_id": 200,
        "bids": [(65000.0 - i, 1.0) for i in range(10)],   # ~1.5% away from binance
        "asks": [(65001.0 + i, 1.0) for i in range(10)],
        "top_only": False,
    }))
    div = h.price_divergence()
    assert div["BTCUSDT"] > 1.0
    health = h.health()
    assert any("venue_price_divergence" in f for f in health["flags"])
    assert health["degraded"] is True


def test_effective_primary_fails_over_when_primary_is_down():
    h = hub()
    for v in ("binance", "coinbase"):
        h.apply(trade(v, 1 if v == "binance" else 2))
    assert h.effective_primary("BTCUSDT") == settings.primary_venue
    h.state[(settings.primary_venue, "BTCUSDT")].book.invalidate("test")
    assert h.effective_primary("BTCUSDT") != settings.primary_venue


def test_single_live_venue_is_degraded():
    h = hub(venues=("binance",))
    h.apply(trade("binance", 1))
    assert h.health()["degraded"] is True            # min_live_venues is 2


# ── anti-spoof metrics ──────────────────────────────────────────────────────

def test_heavy_cancellation_with_no_execution_scores_as_spoof_risk():
    h = hub()
    st = h.state[("binance", "BTCUSDT")]
    uid = 100
    for i in range(40):
        uid += 1
        h.apply(MarketEvent("book_diff", "binance", "BTCUSDT", _now(), {
            "first_update_id": uid, "final_update_id": uid, "prev_update_id": None,
            "bids": [(63990.0 - i * 0.1, 50.0)], "asks": [],
        }))
        uid += 1
        h.apply(MarketEvent("book_diff", "binance", "BTCUSDT", _now(), {
            "first_update_id": uid, "final_update_id": uid, "prev_update_id": None,
            "bids": [(63990.0 - i * 0.1, 0.0)], "asks": [],          # pulled again
        }))
    cancel_rate, dve, risk = h.spoof_metrics(st)
    assert cancel_rate >= 0.45
    assert risk > 0.3


def test_active_two_sided_tape_has_low_spoof_risk():
    h = hub()
    st = h.state[("binance", "BTCUSDT")]
    for i in range(50):
        h.apply(trade("binance", i, qty=2.0))
    cancel_rate, dve, risk = h.spoof_metrics(st)
    assert cancel_rate == 0.0
    assert risk < 0.2


# ── closed candles only ─────────────────────────────────────────────────────

def test_in_progress_candle_is_dropped():
    now = 1_700_000_000_000
    rows = [{"ts": now - 3 * 3_600_000}, {"ts": now - 2 * 3_600_000}, {"ts": now - 3_600_000 + 1}]
    kept = drop_unclosed(rows, "1h", now)
    assert len(kept) == 2                              # the last bar has not closed


def test_exactly_closed_candle_is_kept():
    now = 1_700_000_000_000
    rows = [{"ts": now - 3_600_000}]
    assert drop_unclosed(rows, "1h", now) == rows
