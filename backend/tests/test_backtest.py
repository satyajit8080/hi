"""Backtester behaviour, especially where it must refuse to flatter itself."""
from __future__ import annotations

import pytest

from backtest.engine import Backtester, BacktestConfig
from backtest.metrics import compute
from backtest.walkforward import make_folds, run_walkforward
from tests.conftest import make_candles


def test_backtest_runs_and_produces_trades():
    bt = Backtester(BacktestConfig("BTCUSDT", "1h", warmup=210))
    trades = bt.run(make_candles(700, trend=0.0015, seed=21))
    assert isinstance(trades, list)
    for t in trades:
        assert t["outcome"] in ("WIN", "LOSS", "EXPIRED")
        assert t["entry"] > 0


def test_costs_always_reduce_net_pnl():
    bt = Backtester(BacktestConfig("BTCUSDT", "1h", fee_bps=10, slippage_bps=5, warmup=210))
    trades = bt.run(make_candles(700, trend=0.002, seed=22))
    if not trades:
        pytest.skip("no trades generated for this seed")
    assert all(t["pnl_pct"] < t["gross_pnl_pct"] for t in trades)
    assert all(t["cost_pct"] > 0 for t in trades)


def test_orderflow_is_unavailable_in_backtests():
    """Order flow cannot be reconstructed from OHLCV, so it must not appear."""
    bt = Backtester(BacktestConfig("BTCUSDT", "1h", warmup=210))
    snapshot = bt._snapshot(make_candles(300, seed=1), {}, 299,
                            __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    assert snapshot.micro.available is False
    assert snapshot.context.available is False


def test_walkforward_folds_do_not_overlap_and_embargo_applies():
    folds = make_folds(1000, folds=4, embargo_pct=0.02)
    assert folds
    for f in folds:
        assert f.test_start > f.train_end       # embargo gap present
    for a, b in zip(folds, folds[1:]):
        assert b.test_start >= a.test_end - 1


def test_walkforward_reports_out_of_sample_only():
    report = run_walkforward(make_candles(1600, trend=0.001, seed=31),
                             BacktestConfig("BTCUSDT", "1h", warmup=210), folds=3)
    assert "folds" in report and "combined" in report
    assert "cannot be reconstructed" in report["note"]


def test_metrics_on_empty_trades_are_null_not_zero_dressed_as_success():
    m = compute([])
    assert m.trades == 0 and m.win_rate is None and m.profit_factor is None
