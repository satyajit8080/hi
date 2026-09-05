"""Monte Carlo, PBO, sensitivity, ablation and cost accounting."""
from __future__ import annotations

import numpy as np
import pytest

from backtest import montecarlo, pbo
from backtest.ablation import VARIANTS, run as run_ablation
from backtest.engine import Backtester, BacktestConfig, FeatureStore
from backtest.metrics import breakdown, calibration_quality
from backtest.sensitivity import run as run_sensitivity
from tests.conftest import make_candles


def fake_trades(n=120, seed=1, edge=0.3):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        pnl = float(rng.normal(edge, 2.0))
        out.append({"pnl_pct": pnl, "outcome": "WIN" if pnl > 0 else "LOSS", "strength": int(rng.integers(45, 95)),
                    "regime": rng.choice(["trend_bull", "range"]), "signal_class": "WEAK_LONG",
                    "closed_at": f"2026-{(i % 12) + 1:02d}-01T00:00:00+00:00", "r_multiple": pnl / 2})
    return out


# ── monte carlo ─────────────────────────────────────────────────────────────

def test_montecarlo_refuses_tiny_samples():
    assert montecarlo.run(fake_trades(5))["sufficient"] is False


def test_montecarlo_distribution_brackets_the_observed_path():
    r = montecarlo.run(fake_trades(150), n_sims=500)
    b = r["bootstrap"]
    assert r["sufficient"]
    assert b["total_return_p05"] <= r["observed"]["total_return_pct"] <= b["total_return_p95"] + 15
    assert b["max_drawdown_p50"] <= b["max_drawdown_p95"] <= b["max_drawdown_p99"]
    assert 0 <= b["prob_negative_return"] <= 1
    assert 0 <= r["path_shuffle"]["observed_drawdown_percentile"] <= 100


def test_montecarlo_is_reproducible_with_a_seed():
    a = montecarlo.run(fake_trades(80), n_sims=200, seed=3)
    b = montecarlo.run(fake_trades(80), n_sims=200, seed=3)
    assert a == b


# ── probability of backtest overfitting ─────────────────────────────────────

def test_pbo_is_high_when_configurations_are_pure_noise():
    rng = np.random.default_rng(0)
    matrix = rng.normal(0, 1, size=(256, 20))            # 256 periods, 20 noise configs
    r = pbo.run(matrix, n_blocks=16, max_combinations=120)
    assert r["sufficient"]
    assert r["pbo"] > 0.35                                 # selection is picking noise


def test_pbo_is_low_when_one_configuration_has_real_edge():
    rng = np.random.default_rng(1)
    matrix = rng.normal(0, 1, size=(256, 8))
    matrix[:, 3] += 0.6                                    # a genuinely better config
    r = pbo.run(matrix, n_blocks=16, max_combinations=120)
    assert r["pbo"] < 0.15


def test_pbo_rejects_insufficient_data():
    assert pbo.run(np.zeros((10, 3)))["sufficient"] is False


# ── breakdowns & calibration ────────────────────────────────────────────────

def test_breakdown_by_strength_buckets_and_regime():
    trades = fake_trades(100)
    by_s = breakdown(trades, "strength")
    assert all("-" in k for k in by_s)
    assert sum(v["trades"] for v in by_s.values()) == 100
    by_r = breakdown(trades, "regime")
    assert set(by_r) <= {"trend_bull", "range"}


def test_calibration_quality_is_out_of_sample_and_reports_baseline():
    r = calibration_quality(fake_trades(200))
    assert r["sufficient"] and r["n_train"] + r["n_test"] == 200
    assert "brier_baseline" in r and 0 <= r["brier"] <= 1


def test_calibration_quality_refuses_small_samples():
    assert calibration_quality(fake_trades(20))["sufficient"] is False


# ── cost accounting ─────────────────────────────────────────────────────────

def test_spread_is_charged_on_both_sides():
    cheap = Backtester(BacktestConfig("BTCUSDT", "1h", spread_bps=0.0, fee_bps=0, slippage_bps=0))
    dear = Backtester(BacktestConfig("BTCUSDT", "1h", spread_bps=5.0, fee_bps=0, slippage_bps=0))
    assert dear._cost_pct() == pytest.approx(cheap._cost_pct() + 10.0 / 10_000 * 100)


# ── sensitivity ─────────────────────────────────────────────────────────────

def test_sensitivity_grid_restores_risk_parameters():
    from app.engine import risk as risk_module
    before = dict(risk_module.ATR_MULT_BY_REGIME)
    r = run_sensitivity(make_candles(420, trend=0.001, seed=8), BacktestConfig("BTCUSDT", "1h", warmup=210),
                        strength_floors=(45, 55), cost_bps=(9.5,), atr_scales=(1.0, 1.25))
    assert risk_module.ATR_MULT_BY_REGIME == before
    assert r["grid_size"] == 4 and len(r["rows"]) == 4


# ── ablation ────────────────────────────────────────────────────────────────

def test_ablation_without_recorded_features_collapses_to_baseline_and_says_so():
    r = run_ablation(make_candles(420, trend=0.001, seed=8), BacktestConfig("BTCUSDT", "1h", warmup=210))
    assert set(r["variants"]) == set(VARIANTS)
    assert r["feature_store_empty"] is True
    a = r["variants"]["A_technical"]["metrics"]
    c = r["variants"]["C_technical_orderflow"]["metrics"]
    assert a["trades"] == c["trades"] and a["total_return_pct"] == c["total_return_pct"]
    assert r["verdicts"]["C_technical_orderflow"]["promote"] is False
    assert "No recorded data" in r["verdicts"]["C_technical_orderflow"]["reason"]


def test_feature_store_never_looks_forward():
    fs = FeatureStore(micro={1000: {"obi_5": 0.1}, 2000: {"obi_5": 0.9}}, tolerance_ms=1500)
    assert fs.micro_at(1500)["obi_5"] == 0.1        # the later record is in the future
    assert fs.micro_at(2000)["obi_5"] == 0.9
    assert fs.micro_at(900) is None                  # nothing recorded yet
    assert fs.micro_at(5000) is None                 # too stale to use


def test_backtester_uses_recorded_orderflow_only_when_toggled():
    candles = make_candles(260, trend=0.001, seed=8)
    store = FeatureStore(micro={ts: {"obi_5": 0.3, "obi_persistent": 0.3, "book_synced": True,
                                     "venues_agreeing": 3} for ts in candles.ts})
    off = Backtester(BacktestConfig("BTCUSDT", "1h", warmup=210, use_orderflow=False), store)
    on = Backtester(BacktestConfig("BTCUSDT", "1h", warmup=210, use_orderflow=True), store)
    off.run(candles); on.run(candles)
    assert off.feature_coverage["orderflow"] == 0
    assert on.feature_coverage["orderflow"] > 0
