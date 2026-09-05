"""Parameter sensitivity: does the edge survive nudging the knobs?

Robust parameters sit on a plateau; fragile ones sit on a spike. The grid here
varies the strength floor, the cost assumptions and the ATR stop multiplier, and
reports what fraction of the grid stays positive. A strategy that only works at
one setting is a strategy that does not work.
"""
from __future__ import annotations

import itertools

import numpy as np

from app.engine import risk as risk_module
from app.features.snapshot import Candles
from backtest.engine import Backtester, BacktestConfig
from backtest.metrics import compute


def run(candles: Candles, base: BacktestConfig,
        strength_floors=(40, 45, 50, 55, 60),
        cost_bps=(6.0, 9.5, 14.0),
        atr_scales=(0.8, 1.0, 1.25)) -> dict:
    original = dict(risk_module.ATR_MULT_BY_REGIME)
    rows = []
    try:
        for floor, cost, scale in itertools.product(strength_floors, cost_bps, atr_scales):
            risk_module.ATR_MULT_BY_REGIME.update({k: v * scale for k, v in original.items()})
            cfg = BacktestConfig(**{**base.__dict__, "min_strength": floor,
                                    "fee_bps": cost * 0.55, "slippage_bps": cost * 0.3,
                                    "spread_bps": cost * 0.15})
            trades = Backtester(cfg).run(candles)
            m = compute(trades)
            rows.append({
                "min_strength": floor, "round_trip_cost_bps": cost * 2, "atr_scale": scale,
                "trades": m.trades, "win_rate": m.win_rate, "profit_factor": m.profit_factor,
                "expectancy_pct": m.expectancy_pct, "max_drawdown_pct": m.max_drawdown_pct,
            })
    finally:
        risk_module.ATR_MULT_BY_REGIME.update(original)

    with_trades = [r for r in rows if r["trades"] >= 5]
    positive = [r for r in with_trades if (r["expectancy_pct"] or 0) > 0 and (r["profit_factor"] or 0) > 1]
    expectancies = [r["expectancy_pct"] for r in with_trades if r["expectancy_pct"] is not None]
    return {
        "grid_size": len(rows),
        "cells_with_trades": len(with_trades),
        "robustness": round(len(positive) / len(with_trades), 4) if with_trades else None,
        "expectancy_median": round(float(np.median(expectancies)), 4) if expectancies else None,
        "expectancy_min": round(float(np.min(expectancies)), 4) if expectancies else None,
        "rows": rows,
        "interpretation": (
            "Robustness is the share of parameter cells with positive expectancy and profit factor "
            "above 1. Below ~0.6 the edge depends on a particular setting and should not be trusted."
        ),
    }
