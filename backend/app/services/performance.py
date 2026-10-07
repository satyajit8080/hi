"""Public performance statistics.

Rules that are enforced in code rather than promised in marketing:

  * Only **closed** signals count. Open positions never inflate a win rate.
  * **Nothing is filtered out.** Losses, expiries and cancellations are in the
    same denominator as the wins, and the API returns them all.
  * Simulated signals are excluded from live statistics by default and the
    response always states which set was counted.
  * A statistic below the minimum sample is returned with `sufficient: false`
    rather than as a confident number.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.calibration import (brier_score, expected_calibration_error, log_loss,
                                    reliability_curve, wilson_interval)

WINDOWS = {"30d": 30, "90d": 90, "6m": 182, "1y": 365, "all": None}


@dataclass(slots=True)
class PerformanceStats:
    total: int
    wins: int
    losses: int
    expired: int
    cancelled: int
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None
    avg_return: float | None
    avg_win: float | None
    avg_loss: float | None
    max_drawdown: float | None
    sharpe: float | None
    sortino: float | None
    max_consecutive_wins: int
    max_consecutive_losses: int
    current_streak: int
    current_streak_kind: str
    avg_holding_hours: float | None
    avg_mfe: float | None
    avg_mae: float | None
    sufficient: bool
    ci_low: float | None
    ci_high: float | None

    def to_dict(self) -> dict:
        return {
            "total_signals": self.total,
            "winning_signals": self.wins,
            "losing_signals": self.losses,
            "expired_signals": self.expired,
            "cancelled_signals": self.cancelled,
            "win_rate": _r(self.win_rate, 4),
            "win_rate_ci": [_r(self.ci_low, 4), _r(self.ci_high, 4)],
            "profit_factor": _r(self.profit_factor, 3),
            "expectancy_pct": _r(self.expectancy, 4),
            "avg_return_pct": _r(self.avg_return, 4),
            "avg_win_pct": _r(self.avg_win, 4),
            "avg_loss_pct": _r(self.avg_loss, 4),
            "max_drawdown_pct": _r(self.max_drawdown, 4),
            "sharpe": _r(self.sharpe, 3),
            "sortino": _r(self.sortino, 3),
            "max_consecutive_wins": self.max_consecutive_wins,
            "max_consecutive_losses": self.max_consecutive_losses,
            "current_streak": self.current_streak,
            "current_streak_kind": self.current_streak_kind,
            "avg_holding_hours": _r(self.avg_holding_hours, 2),
            "avg_mfe_pct": _r(self.avg_mfe, 4),
            "avg_mae_pct": _r(self.avg_mae, 4),
            "sufficient_sample": self.sufficient,
            "minimum_sample": settings.min_calibration_sample,
        }


def _r(v, n):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), n)


def compute_stats(rows: list[dict]) -> PerformanceStats:
    """`rows` are closed signals with pnl_pct, outcome, mfe/mae, timestamps."""
    total = len(rows)
    if total == 0:
        return PerformanceStats(0, 0, 0, 0, 0, None, None, None, None, None, None, None, None, None,
                                0, 0, 0, "none", None, None, None, False, None, None)

    wins = [r for r in rows if r["outcome"] == "WIN"]
    losses = [r for r in rows if r["outcome"] == "LOSS"]
    expired = [r for r in rows if r["outcome"] == "EXPIRED"]
    cancelled = [r for r in rows if r["outcome"] == "CANCELLED"]

    # Cancelled signals never had risk on, so they are excluded from the rate
    # but still reported in the counts. Expiries count as non-wins.
    decided = [r for r in rows if r["outcome"] in ("WIN", "LOSS", "EXPIRED")]
    n_decided = len(decided)
    win_rate = len(wins) / n_decided if n_decided else None
    ci_low, ci_high = wilson_interval(len(wins), n_decided) if n_decided else (None, None)

    pnls = np.array([float(r["pnl_pct"] or 0.0) for r in decided], dtype=float)
    gross_profit = float(pnls[pnls > 0].sum())
    gross_loss = float(abs(pnls[pnls < 0].sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (None if gross_profit == 0 else float("inf"))

    avg_win = float(pnls[pnls > 0].mean()) if (pnls > 0).any() else None
    avg_loss = float(pnls[pnls < 0].mean()) if (pnls < 0).any() else None
    avg_return = float(pnls.mean()) if len(pnls) else None
    expectancy = avg_return

    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(np.concatenate(([0.0], equity)))
    drawdown = peak[1:] - equity
    max_dd = float(drawdown.max()) if len(drawdown) else None

    sharpe = sortino = None
    if len(pnls) > 2 and pnls.std(ddof=1) > 0:
        sharpe = float(pnls.mean() / pnls.std(ddof=1) * np.sqrt(len(pnls)))
        downside = pnls[pnls < 0]
        if len(downside) > 1 and downside.std(ddof=1) > 0:
            sortino = float(pnls.mean() / downside.std(ddof=1) * np.sqrt(len(pnls)))

    ordered = sorted(decided, key=lambda r: r["closed_at"] or datetime.min.replace(tzinfo=timezone.utc))
    streaks = [1 if r["outcome"] == "WIN" else 0 for r in ordered]
    max_w = max_l = cur_w = cur_l = 0
    for s in streaks:
        if s:
            cur_w, cur_l = cur_w + 1, 0
        else:
            cur_l, cur_w = cur_l + 1, 0
        max_w, max_l = max(max_w, cur_w), max(max_l, cur_l)
    current_streak = cur_w if cur_w else cur_l
    streak_kind = "wins" if cur_w else ("losses" if cur_l else "none")

    holds = [
        (r["closed_at"] - r["published_at"]).total_seconds() / 3600.0
        for r in decided
        if r.get("closed_at") and r.get("published_at")
    ]

    return PerformanceStats(
        total=total,
        wins=len(wins),
        losses=len(losses),
        expired=len(expired),
        cancelled=len(cancelled),
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        avg_return=avg_return,
        avg_win=avg_win,
        avg_loss=avg_loss,
        max_drawdown=max_dd,
        sharpe=sharpe,
        sortino=sortino,
        max_consecutive_wins=max_w,
        max_consecutive_losses=max_l,
        current_streak=current_streak,
        current_streak_kind=streak_kind,
        avg_holding_hours=float(np.mean(holds)) if holds else None,
        avg_mfe=float(np.mean([float(r.get("mfe_pct") or 0) for r in decided])) if decided else None,
        avg_mae=float(np.mean([float(r.get("mae_pct") or 0) for r in decided])) if decided else None,
        sufficient=n_decided >= settings.min_calibration_sample,
        ci_low=ci_low,
        ci_high=ci_high,
    )


async def fetch_closed(
    session: AsyncSession,
    window: str = "all",
    symbol: str | None = None,
    timeframe: str | None = None,
    include_simulated: bool | None = None,
) -> list[dict]:
    if include_simulated is None:
        include_simulated = settings.is_replay
    days = WINDOWS.get(window)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    rows = await session.execute(
        text(
            """
            SELECT signal_id, symbol, timeframe, direction, regime, strength,
                   calibrated_winrate, outcome, pnl_pct, r_multiple, mfe_pct, mae_pct,
                   published_at, closed_at, close_reason, is_simulated, strategy_version
            FROM v_closed_signals
            WHERE (CAST(:since AS timestamptz) IS NULL OR closed_at >= :since)
              AND (CAST(:symbol AS text) IS NULL OR symbol = :symbol)
              AND (CAST(:timeframe AS text) IS NULL OR timeframe = :timeframe)
              AND (:include_sim OR NOT is_simulated)
            ORDER BY closed_at
            """
        ),
        {"since": since, "symbol": symbol, "timeframe": timeframe, "include_sim": include_simulated},
    )
    return [dict(r._mapping) for r in rows]


async def performance_report(
    session: AsyncSession, window: str = "all", include_simulated: bool | None = None
) -> dict:
    if include_simulated is None:
        include_simulated = settings.is_replay
    rows = await fetch_closed(session, window, include_simulated=include_simulated)
    overall = compute_stats(rows).to_dict()

    by_symbol = {
        sym: compute_stats([r for r in rows if r["symbol"] == sym]).to_dict()
        for sym in sorted({r["symbol"] for r in rows})
    }
    by_timeframe = {
        tf: compute_stats([r for r in rows if r["timeframe"] == tf]).to_dict()
        for tf in sorted({r["timeframe"] for r in rows})
    }
    by_regime = {
        rg: compute_stats([r for r in rows if r["regime"] == rg]).to_dict()
        for rg in sorted({r["regime"] for r in rows})
    }
    def _bucket(st: int) -> str:
        lo = (int(st) // 10) * 10
        return f"{lo}-{lo + 9}"
    by_strength_bucket = {
        b: compute_stats([r for r in rows if _bucket(r["strength"]) == b]).to_dict()
        for b in sorted({_bucket(r["strength"]) for r in rows})
    }

    months: dict[str, list[dict]] = {}
    for r in rows:
        if r["closed_at"]:
            months.setdefault(r["closed_at"].strftime("%Y-%m"), []).append(r)
    by_month = [
        {"month": m, **compute_stats(v).to_dict()} for m, v in sorted(months.items())
    ]

    equity: list[dict] = []
    running = 0.0
    peak = 0.0
    for r in rows:
        if r["outcome"] == "CANCELLED":
            continue
        running += float(r["pnl_pct"] or 0.0)
        peak = max(peak, running)
        equity.append({
            "ts": r["closed_at"].isoformat() if r["closed_at"] else None,
            "cumulative_pct": round(running, 4),
            "drawdown_pct": round(peak - running, 4),
            "signal_id": str(r["signal_id"]),
            "outcome": r["outcome"],
        })

    calibrated = [r for r in rows if r["calibrated_winrate"] is not None and r["outcome"] in ("WIN", "LOSS", "EXPIRED")]
    calibration: list = []
    calibration_quality: dict = {"sufficient": False, "n": len(calibrated)}
    if len(calibrated) >= 20:
        probs = np.array([float(r["calibrated_winrate"]) for r in calibrated])
        labels = np.array([1.0 if r["outcome"] == "WIN" else 0.0 for r in calibrated])
        calibration = reliability_curve(probs, labels)
        base = np.full(len(labels), float(labels.mean()))
        calibration_quality = {
            "sufficient": True, "n": len(calibrated),
            "brier": round(brier_score(probs, labels), 5),
            "brier_baseline": round(brier_score(base, labels), 5),
            "log_loss": round(log_loss(probs, labels), 5),
            "ece": round(expected_calibration_error(probs, labels), 5),
            "skill": round(1.0 - brier_score(probs, labels) / max(brier_score(base, labels), 1e-9), 4),
            "note": "Skill above 0 means the quoted confidence beat simply predicting the base rate.",
        }

    return {
        "window": window,
        "overall": overall,
        "by_symbol": by_symbol,
        "by_timeframe": by_timeframe,
        "by_regime": by_regime,
        "by_strength_bucket": by_strength_bucket,
        "by_month": by_month,
        "equity_curve": equity,
        "calibration_curve": calibration,
        "calibration_sample": len(calibrated),
        "calibration_quality": calibration_quality,
        "includes_simulated": include_simulated,
        "disclaimer": (
            "Every closed signal is included: wins, losses, expiries and cancellations. "
            "Nothing is removed after publication. Past performance does not predict future results."
            + (
                " THIS DATASET IS SIMULATED — it is generated market data for development and "
                "demonstration, not a live track record."
                if include_simulated
                else ""
            )
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def strength_outcomes(session: AsyncSession, symbol: str, timeframe: str) -> list[tuple[int, bool]]:
    """(strength, was_win) pairs used to calibrate new signals."""
    rows = await session.execute(
        text(
            """
            SELECT strength, outcome
            FROM v_closed_signals
            WHERE symbol = :symbol AND timeframe = :timeframe
              AND outcome IN ('WIN','LOSS','EXPIRED')
              AND strategy_version = :version
            """
        ),
        {"symbol": symbol, "timeframe": timeframe, "version": settings.strategy_version},
    )
    return [(int(r.strength), r.outcome == "WIN") for r in rows]
