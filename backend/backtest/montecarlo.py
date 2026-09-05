"""Monte Carlo on the trade sequence.

A single equity curve is one draw from a distribution. Resampling the closed
trades — with replacement (bootstrap) and without (path shuffle) — shows how
wide the drawdown distribution really is and how often a sequence this good
would have looked like a disaster in a different order.
"""
from __future__ import annotations

import numpy as np


def _max_drawdown(pnl: np.ndarray) -> float:
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate(([0.0], equity)))
    return float((peak[1:] - equity).max()) if len(pnl) else 0.0


def run(trades: list[dict], n_sims: int = 2000, seed: int = 7, ruin_pct: float = 25.0) -> dict:
    pnl = np.array([float(t["pnl_pct"]) for t in trades if t.get("pnl_pct") is not None])
    if len(pnl) < 10:
        return {"sufficient": False, "n_trades": int(len(pnl)),
                "note": "Fewer than 10 closed trades; Monte Carlo is not meaningful."}

    rng = np.random.default_rng(seed)
    n = len(pnl)
    boot_dd = np.empty(n_sims)
    boot_ret = np.empty(n_sims)
    shuf_dd = np.empty(n_sims)
    ruin = 0
    for i in range(n_sims):
        b = rng.choice(pnl, size=n, replace=True)
        boot_dd[i] = _max_drawdown(b)
        boot_ret[i] = float(b.sum())
        if boot_dd[i] >= ruin_pct:
            ruin += 1
        shuf_dd[i] = _max_drawdown(rng.permutation(pnl))

    pct = lambda a, q: round(float(np.percentile(a, q)), 4)  # noqa: E731
    return {
        "sufficient": True,
        "n_trades": int(n),
        "n_sims": n_sims,
        "observed": {"total_return_pct": round(float(pnl.sum()), 4),
                     "max_drawdown_pct": round(_max_drawdown(pnl), 4)},
        "bootstrap": {
            "total_return_p05": pct(boot_ret, 5), "total_return_p50": pct(boot_ret, 50),
            "total_return_p95": pct(boot_ret, 95),
            "max_drawdown_p50": pct(boot_dd, 50), "max_drawdown_p95": pct(boot_dd, 95),
            "max_drawdown_p99": pct(boot_dd, 99),
            "prob_negative_return": round(float((boot_ret < 0).mean()), 4),
            f"prob_drawdown_over_{int(ruin_pct)}pct": round(ruin / n_sims, 4),
        },
        "path_shuffle": {
            "max_drawdown_p50": pct(shuf_dd, 50), "max_drawdown_p95": pct(shuf_dd, 95),
            "observed_drawdown_percentile": round(float((shuf_dd <= _max_drawdown(pnl)).mean() * 100), 1),
        },
        "note": (
            "Bootstrap resamples trades with replacement; path shuffle keeps the same trades in a "
            "different order. If the observed drawdown sits at a low percentile of the shuffle "
            "distribution, the historical sequence was luckier than the strategy."
        ),
    }
