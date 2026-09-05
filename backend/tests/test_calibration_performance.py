"""Calibration honesty and performance aggregation."""
from __future__ import annotations

import numpy as np
import pytest

from app.engine.calibration import (
    PlattCalibrator,
    brier_score,
    empirical_bucket,
    expected_calibration_error,
    reliability_curve,
    wilson_interval,
)
from app.services.performance import compute_stats


def test_wilson_interval_widens_on_small_samples():
    lo_small, hi_small = wilson_interval(3, 5)
    lo_big, hi_big = wilson_interval(300, 500)
    assert (hi_small - lo_small) > (hi_big - lo_big)


def test_wilson_interval_brackets_the_point_estimate():
    lo, hi = wilson_interval(60, 100)
    assert lo < 0.6 < hi


def test_small_sample_returns_no_win_rate_at_all():
    """Refusing to quote a number is the feature, not a limitation."""
    outcomes = [(70, True), (72, True), (68, False)]
    result = empirical_bucket(70, outcomes)
    assert result.sufficient is False
    assert result.win_rate is None
    assert "at least" in result.note


def test_sufficient_sample_produces_a_rate_with_an_interval():
    outcomes = [(70, i % 3 != 0) for i in range(60)]
    result = empirical_bucket(70, outcomes)
    assert result.sufficient is True
    assert 0 < result.win_rate < 1
    assert result.ci_low < result.win_rate < result.ci_high


def test_platt_produces_monotonic_probabilities():
    rng = np.random.default_rng(0)
    scores = rng.uniform(-3, 3, 400)
    labels = (scores + rng.normal(0, 0.8, 400) > 0).astype(float)
    platt = PlattCalibrator().fit(scores, labels)
    assert platt.predict(-2.0) < platt.predict(0.0) < platt.predict(2.0)


def test_calibration_improves_brier_over_raw_scores():
    rng = np.random.default_rng(1)
    scores = rng.uniform(-3, 3, 500)
    labels = (scores + rng.normal(0, 1.0, 500) > 0).astype(float)
    raw = np.clip((scores + 3) / 6, 0, 1)
    platt = PlattCalibrator().fit(scores, labels)
    calibrated = np.array([platt.predict(s) for s in scores])
    assert brier_score(calibrated, labels) <= brier_score(raw, labels)


def test_ece_is_zero_for_a_perfectly_calibrated_predictor():
    probs = np.array([0.0] * 100 + [1.0] * 100)
    labels = np.array([0.0] * 100 + [1.0] * 100)
    assert expected_calibration_error(probs, labels) == pytest.approx(0.0, abs=1e-9)


def test_reliability_curve_reports_empty_bins_honestly():
    probs = np.array([0.9] * 30)
    labels = np.array([1.0] * 30)
    curve = reliability_curve(probs, labels)
    empty = [b for b in curve if b["n"] == 0]
    assert empty and all(b["observed"] is None for b in empty)


# ── performance aggregation ─────────────────────────────────────────────────

def closed(outcome, pnl, **kw):
    from datetime import datetime, timedelta, timezone
    opened = datetime.now(timezone.utc) - timedelta(hours=6)
    base = {
        "outcome": outcome, "pnl_pct": pnl, "symbol": "BTCUSDT", "timeframe": "1h",
        "regime": "trend_bull", "published_at": opened, "closed_at": opened + timedelta(hours=3),
        "mfe_pct": 2.0, "mae_pct": -1.0, "strength": 70, "calibrated_winrate": 0.6,
    }
    base.update(kw)
    return base


def test_losses_are_included_in_the_denominator():
    rows = [closed("WIN", 2.0) for _ in range(6)] + [closed("LOSS", -1.0) for _ in range(4)]
    stats = compute_stats(rows)
    assert stats.total == 10 and stats.win_rate == pytest.approx(0.6)


def test_expiries_count_against_the_win_rate():
    rows = [closed("WIN", 2.0) for _ in range(5)] + [closed("EXPIRED", 0.1) for _ in range(5)]
    assert compute_stats(rows).win_rate == pytest.approx(0.5)


def test_cancelled_signals_are_counted_but_excluded_from_the_rate():
    rows = [closed("WIN", 2.0), closed("LOSS", -1.0), closed("CANCELLED", 0.0)]
    stats = compute_stats(rows)
    assert stats.total == 3 and stats.cancelled == 1
    assert stats.win_rate == pytest.approx(0.5)


def test_profit_factor_matches_definition():
    rows = [closed("WIN", 3.0), closed("WIN", 3.0), closed("LOSS", -2.0)]
    assert compute_stats(rows).profit_factor == pytest.approx(3.0)


def test_max_drawdown_is_measured_from_the_running_peak():
    rows = [closed("WIN", 5.0), closed("LOSS", -3.0), closed("LOSS", -4.0), closed("WIN", 2.0)]
    assert compute_stats(rows).max_drawdown == pytest.approx(7.0)


def test_consecutive_losses_are_counted():
    rows = [closed("WIN", 1.0)] + [closed("LOSS", -1.0) for _ in range(4)] + [closed("WIN", 1.0)]
    assert compute_stats(rows).max_consecutive_losses == 4


def test_empty_input_is_handled_without_fake_numbers():
    stats = compute_stats([])
    assert stats.total == 0 and stats.win_rate is None and stats.sufficient is False
