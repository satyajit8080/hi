"""Scoring rules that the product's honesty depends on."""
from __future__ import annotations

import pytest

from app.engine.scoring import (
    MICRO_WEIGHT_BY_TF,
    STRATEGY_WEIGHTS,
    micro_weight_for_timeframe,
    score_snapshot,
)
from app.features.snapshot import ContextFeatures, MicroFeatures
from tests.conftest import make_candles, make_snapshot


def strong_micro(**kw) -> MicroFeatures:
    defaults = dict(
        venue="binance", ts_ms=1, available=True, book_synced=True, venues_agreeing=3,
        obi_5=0.6, obi_20=0.6, obi_persistent=0.6, ofi_z=3.0, trade_imbalance=0.6,
        cvd_divergence=0.0, spread_z=0.2, micro_price=64000.0, mid=64000.0,
    )
    defaults.update(kw)
    return MicroFeatures(**defaults)


def test_component_weights_sum_to_one():
    assert sum(STRATEGY_WEIGHTS.values()) == pytest.approx(1.0)


def test_microstructure_weight_is_zero_above_one_hour():
    for tf in ("1h", "4h", "1d"):
        assert micro_weight_for_timeframe(tf) == 0.0
    assert MICRO_WEIGHT_BY_TF["5m"] > MICRO_WEIGHT_BY_TF["15m"] > 0


def test_orderflow_cannot_move_a_four_hour_signal():
    """Horizon discipline: a feature that decays in minutes gets no vote on 4h."""
    bull = make_candles(trend=0.002, seed=5)
    base = score_snapshot(make_snapshot("4h", bull))
    with_flow = score_snapshot(make_snapshot("4h", bull, micro=strong_micro()))
    assert with_flow.component_scores["order_flow"] == 0.0
    assert with_flow.raw_score == pytest.approx(base.raw_score, rel=1e-9)


def test_orderflow_does_move_a_five_minute_signal():
    bull = make_candles(trend=0.002, seed=5)
    base = score_snapshot(make_snapshot("5m", bull))
    with_flow = score_snapshot(make_snapshot("5m", bull, micro=strong_micro()))
    assert with_flow.component_scores["order_flow"] != 0.0
    assert with_flow.raw_score != pytest.approx(base.raw_score)


def test_context_reason_codes_never_carry_contribution():
    ctx = ContextFeatures(
        smart_money_accum_z=3.5, smart_money_cohort_size=42,
        exchange_netflow_usd=-25_000_000, is_signal_driver=False, available=True,
    )
    result = score_snapshot(make_snapshot("1h", context=ctx))
    context_codes = [r for r in result.reason_codes if r["group"] == "context_onchain"]
    assert context_codes, "expected smart-money codes to be emitted for the UI"
    assert all(r["contribution"] == 0.0 for r in context_codes)
    assert all(r["role"] == "context_only" for r in context_codes)
    assert all(r.get("note") for r in context_codes)


def test_unvalidated_context_does_not_change_the_score():
    plain = score_snapshot(make_snapshot("1h"))
    with_ctx = score_snapshot(make_snapshot("1h", context=ContextFeatures(
        smart_money_accum_z=5.0, smart_money_cohort_size=10, is_signal_driver=False, available=True,
    )))
    assert with_ctx.raw_score == pytest.approx(plain.raw_score, rel=1e-9)


def test_strength_is_bounded():
    for seed in range(6):
        r = score_snapshot(make_snapshot("1h", make_candles(trend=0.004, seed=seed)))
        assert 0 <= r.strength <= 100


def test_uptrend_scores_long_and_downtrend_scores_short():
    up = score_snapshot(make_snapshot("1h", make_candles(trend=0.004, noise=0.002, seed=11)))
    down = score_snapshot(make_snapshot("1h", make_candles(trend=-0.004, noise=0.002, seed=11)))
    assert up.direction == "LONG"
    assert down.direction == "SHORT"


def test_wide_spread_is_recorded_as_a_risk_not_a_direction():
    r = score_snapshot(make_snapshot("5m", micro=strong_micro(spread_z=3.5)))
    assert any(f["code"] == "SPREAD_ELEVATED" for f in r.risk_flags)


def test_single_venue_flow_is_penalised():
    both = score_snapshot(make_snapshot("5m", micro=strong_micro(venues_agreeing=3)))
    alone = score_snapshot(make_snapshot("5m", micro=strong_micro(venues_agreeing=1)))
    assert abs(alone.component_scores["order_flow"]) < abs(both.component_scores["order_flow"])
    assert any(f["code"] == "SINGLE_VENUE_FLOW" for f in alone.risk_flags)


def test_cvd_divergence_dampens_rather_than_reverses():
    clean = score_snapshot(make_snapshot("5m", micro=strong_micro(cvd_divergence=0.0)))
    diverging = score_snapshot(make_snapshot("5m", micro=strong_micro(cvd_divergence=-0.8)))
    assert abs(diverging.component_scores["order_flow"]) < abs(clean.component_scores["order_flow"])
    assert any(f["code"] == "CVD_DIVERGENCE" for f in diverging.risk_flags)


def test_reason_codes_are_sorted_by_absolute_contribution():
    r = score_snapshot(make_snapshot("1h", make_candles(trend=0.003, seed=2)))
    contributions = [abs(x["contribution"]) for x in r.reason_codes]
    assert contributions == sorted(contributions, reverse=True)
