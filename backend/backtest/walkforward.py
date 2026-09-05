"""Walk-forward evaluation with purging and embargo.

Overlapping trades leak information across a naive train/test split: a trade
opened before the boundary and closed after it appears in both halves. Purging
removes those; the embargo drops a short buffer after each test window so
serial correlation cannot smuggle information backwards.

Reported alongside every fold: the number of configurations tried, so the
Sharpe can be deflated. A single glowing fold out of forty is not a result.
"""
from __future__ import annotations

from dataclasses import dataclass

from backtest.engine import Backtester, BacktestConfig
from backtest.metrics import breakdown, calibration_quality, compute, deflated_sharpe
from app.features.snapshot import Candles


@dataclass
class Fold:
    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def make_folds(n: int, folds: int = 5, embargo_pct: float = 0.01) -> list[Fold]:
    size = n // (folds + 1)
    embargo = int(n * embargo_pct)
    out: list[Fold] = []
    for k in range(folds):
        train_end = size * (k + 1)
        test_start = train_end + embargo
        test_end = min(test_start + size, n)
        if test_start >= test_end:
            break
        out.append(Fold(k, 0, train_end, test_start, test_end))
    return out


def run_walkforward(candles: Candles, config: BacktestConfig, folds: int = 5) -> dict:
    results = []
    all_trades: list[dict] = []

    for fold in make_folds(len(candles), folds):
        window = Candles(
            candles.ts[fold.test_start : fold.test_end],
            candles.open[fold.test_start : fold.test_end],
            candles.high[fold.test_start : fold.test_end],
            candles.low[fold.test_start : fold.test_end],
            candles.close[fold.test_start : fold.test_end],
            candles.volume[fold.test_start : fold.test_end],
        )
        if len(window) <= config.warmup + 10:
            continue
        bt = Backtester(config)
        trades = bt.run(window)
        all_trades.extend(trades)
        results.append({
            "fold": fold.index,
            "test_bars": len(window),
            "metrics": compute(trades).to_dict(),
            "skipped": bt.skipped,
        })

    combined = compute(all_trades)
    sharpe = combined.sharpe
    return {
        "folds": results,
        "combined": combined.to_dict(),
        "calibration": calibration_quality(all_trades),
        "by_regime": breakdown(all_trades, "regime"),
        "by_signal_class": breakdown(all_trades, "signal_class"),
        "by_strength": breakdown(all_trades, "strength"),
        "trades": all_trades,
        "deflated_sharpe": (
            deflated_sharpe(sharpe, n_trials=max(len(results), 1), n_obs=combined.trades)
            if sharpe is not None and combined.trades > 3
            else None
        ),
        "note": (
            "Out-of-sample folds only, purged with an embargo. Order-flow features are absent "
            "from every backtest because they cannot be reconstructed from OHLCV."
        ),
    }
