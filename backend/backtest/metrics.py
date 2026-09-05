"""Backtest performance metrics.

Same definitions the live performance service uses, so a backtest number and a
live number mean the same thing. Where they would differ, the backtest is the
one that has to change.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class BacktestMetrics:
    trades: int
    wins: int
    losses: int
    expired: int
    win_rate: float | None
    profit_factor: float | None
    expectancy_pct: float | None
    avg_win_pct: float | None
    avg_loss_pct: float | None
    max_drawdown_pct: float | None
    sharpe: float | None
    sortino: float | None
    max_consecutive_losses: int
    avg_r_multiple: float | None
    total_return_pct: float
    exposure_bars: int
    net_of_costs: bool

    def to_dict(self) -> dict:
        return asdict(self)


def compute(trades: list[dict], net_of_costs: bool = True) -> BacktestMetrics:
    if not trades:
        return BacktestMetrics(0, 0, 0, 0, None, None, None, None, None, None, None, None,
                               0, None, 0.0, 0, net_of_costs)

    pnl = np.array([t["pnl_pct"] for t in trades], dtype=float)
    outcomes = [t["outcome"] for t in trades]
    wins = sum(1 for o in outcomes if o == "WIN")
    losses = sum(1 for o in outcomes if o == "LOSS")
    expired = sum(1 for o in outcomes if o == "EXPIRED")

    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(abs(pnl[pnl < 0].sum()))

    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate(([0.0], equity)))
    max_dd = float((peak[1:] - equity).max())

    sharpe = sortino = None
    if len(pnl) > 2 and pnl.std(ddof=1) > 0:
        sharpe = float(pnl.mean() / pnl.std(ddof=1) * np.sqrt(len(pnl)))
        downside = pnl[pnl < 0]
        if len(downside) > 1 and downside.std(ddof=1) > 0:
            sortino = float(pnl.mean() / downside.std(ddof=1) * np.sqrt(len(pnl)))

    streak = worst = 0
    for o in outcomes:
        streak = streak + 1 if o != "WIN" else 0
        worst = max(worst, streak)

    r_multiples = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]

    return BacktestMetrics(
        trades=len(trades),
        wins=wins,
        losses=losses,
        expired=expired,
        win_rate=wins / len(trades),
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else None,
        expectancy_pct=float(pnl.mean()),
        avg_win_pct=float(pnl[pnl > 0].mean()) if (pnl > 0).any() else None,
        avg_loss_pct=float(pnl[pnl < 0].mean()) if (pnl < 0).any() else None,
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        sortino=sortino,
        max_consecutive_losses=worst,
        avg_r_multiple=float(np.mean(r_multiples)) if r_multiples else None,
        total_return_pct=float(pnl.sum()),
        exposure_bars=int(sum(t.get("bars_held", 0) for t in trades)),
        net_of_costs=net_of_costs,
    )


def breakdown(trades: list[dict], key: str) -> dict[str, dict]:
    """Metrics grouped by a trade attribute (regime, signal_class, strength bucket)."""
    groups: dict[str, list[dict]] = {}
    for t in trades:
        k = str(t.get(key, "unknown"))
        if key == "strength":
            k = f"{(int(t['strength']) // 10) * 10}-{(int(t['strength']) // 10) * 10 + 9}"
        groups.setdefault(k, []).append(t)
    return {k: compute(v).to_dict() for k, v in sorted(groups.items())}


def calibration_quality(trades: list[dict], train_frac: float = 0.6) -> dict:
    """Fit Platt on the earlier trades, score the later ones. Never in-sample.

    Reports Brier, log loss and ECE for the held-out portion, plus the naive
    baseline (predict the training win rate for everyone) so the reader can see
    whether strength actually carries information beyond the base rate.
    """
    from app.engine.calibration import (
        PlattCalibrator, brier_score, expected_calibration_error, log_loss,
    )

    usable = [t for t in trades if t["outcome"] in ("WIN", "LOSS", "EXPIRED")]
    n = len(usable)
    if n < 40:
        return {"sufficient": False, "n": n, "note": "Fewer than 40 closed trades; calibration not assessed."}
    split = int(n * train_frac)
    train, test = usable[:split], usable[split:]
    xs = np.array([t["strength"] / 100.0 for t in train])
    ys = np.array([1.0 if t["outcome"] == "WIN" else 0.0 for t in train])
    platt = PlattCalibrator().fit(xs, ys)
    xt = np.array([t["strength"] / 100.0 for t in test])
    yt = np.array([1.0 if t["outcome"] == "WIN" else 0.0 for t in test])
    probs = np.array([platt.predict(x) for x in xt])
    base = np.full(len(yt), float(ys.mean()))
    return {
        "sufficient": True,
        "n_train": len(train),
        "n_test": len(test),
        "brier": round(brier_score(probs, yt), 5),
        "brier_baseline": round(brier_score(base, yt), 5),
        "log_loss": round(log_loss(probs, yt), 5),
        "log_loss_baseline": round(log_loss(base, yt), 5),
        "ece": round(expected_calibration_error(probs, yt), 5),
        "skill": round(1.0 - brier_score(probs, yt) / max(brier_score(base, yt), 1e-9), 4),
    }


def deflated_sharpe(sharpe: float, n_trials: int, n_obs: int) -> float:
    """Haircut a Sharpe ratio for the number of configurations tried.

    Testing 200 parameter sets guarantees one looks excellent. Reporting that
    one without this correction is the single most common way backtests lie.
    """
    if n_obs < 3 or n_trials < 1:
        return float("nan")
    euler = 0.5772156649
    expected_max = (1 - euler) * _norm_ppf(1 - 1.0 / n_trials) + euler * _norm_ppf(
        1 - 1.0 / (n_trials * math.e)
    )
    return float((sharpe - expected_max) * math.sqrt(max(n_obs - 1, 1)))


import math  # noqa: E402


def _norm_ppf(p: float) -> float:
    """Acklam's inverse normal CDF approximation."""
    if not 0 < p < 1:
        return 0.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
