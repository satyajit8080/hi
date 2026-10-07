"""Risk plan construction and the conservative lifecycle rules."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.lifecycle import Bar, SignalPosition, advance
from app.engine.risk import (
    RiskRejection,
    build_risk_plan,
    data_quality_gate,
    run_gates,
    strength_gate,
)
from tests.conftest import make_candles, make_snapshot


def position(direction="LONG", **kw) -> SignalPosition:
    base = dict(
        signal_id="s1", direction=direction, entry=100.0,
        stop_loss=95.0 if direction == "LONG" else 105.0,
        tp1=105.0 if direction == "LONG" else 95.0,
        tp2=110.0 if direction == "LONG" else 90.0,
        tp3=115.0 if direction == "LONG" else 85.0,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        status="ACTIVE", tp_hits=0, mfe_pct=0.0, mae_pct=0.0,
    )
    base.update(kw)
    return SignalPosition(**base)


def bar(high, low, close=None, ts=None) -> Bar:
    return Bar(ts=ts or datetime.now(timezone.utc), high=high, low=low, close=close or (high + low) / 2)


# ── risk plan ───────────────────────────────────────────────────────────────

def test_long_plan_has_correct_level_ordering():
    plan = build_risk_plan(make_snapshot("1h", make_candles(trend=0.003, seed=4)), "LONG", 70)
    assert plan.stop_loss < plan.entry < plan.tp1 < plan.tp2 < plan.tp3


def test_short_plan_has_correct_level_ordering():
    plan = build_risk_plan(make_snapshot("1h", make_candles(trend=-0.003, seed=4)), "SHORT", 70)
    assert plan.stop_loss > plan.entry > plan.tp1 > plan.tp2 > plan.tp3


def test_plan_meets_the_minimum_risk_reward():
    plan = build_risk_plan(make_snapshot("1h", make_candles(trend=0.003, seed=6)), "LONG", 70)
    assert plan.risk_reward >= 1.5


def test_expiry_is_in_the_future_and_scales_with_timeframe():
    fast = build_risk_plan(make_snapshot("15m", make_candles(trend=0.003, seed=8)), "LONG", 70)
    slow = build_risk_plan(make_snapshot("4h", make_candles(trend=0.003, seed=8)), "LONG", 70)
    now = datetime.now(timezone.utc)
    assert fast.expires_at > now and slow.expires_at > fast.expires_at


def test_missing_atr_is_rejected_rather_than_guessed():
    snap = make_snapshot("1h")
    snap.ta["atr14"] = float("nan")
    with pytest.raises(RiskRejection):
        build_risk_plan(snap, "LONG", 70)


# ── gates ───────────────────────────────────────────────────────────────────

def test_degraded_data_blocks_publication():
    gate = data_quality_gate(make_snapshot("1h", degraded=True))
    assert gate.passed is False and gate.code == "DATA_DEGRADED"


def test_single_venue_blocks_publication():
    gate = data_quality_gate(make_snapshot("1h", venues_live=1))
    assert gate.passed is False and gate.code == "INSUFFICIENT_VENUES"


def test_weak_signal_is_not_published():
    assert strength_gate(20).passed is False
    assert strength_gate(80).passed is True


def test_run_gates_passes_on_clean_data():
    assert run_gates(make_snapshot("1h"), 70).passed is True


# ── lifecycle ───────────────────────────────────────────────────────────────

def test_stop_wins_when_a_bar_contains_both_stop_and_target():
    """The rule the whole track record's credibility rests on."""
    update = advance(position(), bar(high=112.0, low=94.0))
    assert update.status == "STOPPED"
    assert update.outcome == "LOSS"
    assert "stop-first" in (update.close_reason or "").lower() or "ordering" in (update.close_reason or "").lower()


def test_clean_target_hit_is_recorded():
    update = advance(position(), bar(high=106.0, low=99.0))
    assert update.tp_hits == 1
    assert update.outcome is None       # still open, targets remain


def test_all_targets_closes_as_a_win():
    update = advance(position(), bar(high=116.0, low=99.0))
    assert update.tp_hits == 3 and update.outcome == "WIN"


def test_stop_after_tp1_is_a_win_not_a_loss():
    update = advance(position(tp_hits=1), bar(high=101.0, low=94.0))
    assert update.status == "STOPPED" and update.outcome == "WIN"


def test_short_direction_mirrors_correctly():
    update = advance(position("SHORT"), bar(high=99.0, low=84.0))
    assert update.tp_hits == 3 and update.outcome == "WIN"


def test_short_stop_triggers_on_high():
    update = advance(position("SHORT"), bar(high=106.0, low=101.0))
    assert update.status == "STOPPED" and update.outcome == "LOSS"


def test_mfe_and_mae_are_tracked_in_the_right_direction():
    update = advance(position(), bar(high=108.0, low=97.0))
    assert update.mfe_pct == pytest.approx(8.0, abs=0.01)
    assert update.mae_pct == pytest.approx(-3.0, abs=0.01)


def test_expiry_without_a_target_is_not_a_win():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    update = advance(position(expires_at=past), bar(high=101.0, low=99.0, close=100.5))
    assert update.status == "EXPIRED" and update.outcome == "EXPIRED"


def test_pnl_is_signed_correctly_for_shorts():
    update = advance(position("SHORT"), bar(high=99.0, low=84.0))
    assert update.pnl_pct is not None and update.pnl_pct > 0


def test_free_tier_redaction_locks_fresh_levels():
    from app.services.subscriptions import redact_for_tier

    fresh = {"published_at": datetime.now(timezone.utc).isoformat(), "entry": 1.0, "stop_loss": 0.9,
             "tp1": 1.1, "tp2": 1.2, "tp3": 1.3}
    out = redact_for_tier(fresh, "free")
    assert out["locked"] and out["entry"] is None and out["tp3"] is None
    assert redact_for_tier(fresh, "pro")["entry"] == 1.0
