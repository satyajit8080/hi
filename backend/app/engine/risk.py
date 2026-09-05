"""Risk engine: levels, gates, and the reasons a signal is refused.

Stops come from two independent methods and we take the *wider* (more
conservative) of the two:

  * ATR distance — "how far is far, given current volatility"
  * structure    — "at what price was the idea actually wrong"

Targets are the *nearer* of an R-multiple target and the next structural level,
so we never advertise a target that sits on the far side of obvious resistance.

Levels are never tuned to flatter a backtest. The method is fixed first and
measured afterwards; that ordering is the whole point.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from app.config import settings
from app.features.snapshot import FeatureSnapshot
from app.features.structure import structure_stop

# Signal validity by timeframe. A 15m setup that has not triggered in a few
# hours is not a setup any more.
EXPIRY_BY_TF: dict[str, timedelta] = {
    "5m": timedelta(minutes=45),
    "15m": timedelta(hours=3),
    "30m": timedelta(hours=6),
    "1h": timedelta(hours=12),
    "4h": timedelta(hours=36),
    "1d": timedelta(days=5),
}

ATR_MULT_BY_REGIME: dict[str, float] = {
    "trend_bull": 1.8,
    "trend_bear": 1.8,
    "range": 1.5,
    "high_volatility": 2.5,
}


@dataclass(slots=True)
class RiskPlan:
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk_reward: float
    risk_category: str
    expires_at: datetime
    invalidation: str
    stop_method: str
    notes: list[str]


class RiskRejection(Exception):
    """Raised when a setup cannot be published as a tradeable plan."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_risk_plan(s: FeatureSnapshot, direction: str, strength: int) -> RiskPlan:
    price = float(s.ta["close"])
    atr = float(s.ta.get("atr14") or 0.0)
    if not np.isfinite(atr) or atr <= 0:
        raise RiskRejection("NO_ATR", "ATR is unavailable, so stop distance cannot be sized.")

    # Entry: use the micro-price when order flow is fresh and the timeframe is
    # fast enough for it to mean anything; otherwise the close.
    entry = price
    entry_source = "close"
    if s.micro.available and s.micro.book_synced and np.isfinite(s.micro.micro_price):
        if s.timeframe in ("5m", "15m") and abs(s.micro.micro_price - price) / price < 0.002:
            entry = float(s.micro.micro_price)
            entry_source = "micro_price"

    mult = ATR_MULT_BY_REGIME.get(s.regime.regime, 1.8)
    atr_stop = entry - mult * atr if direction == "LONG" else entry + mult * atr
    struct_stop = structure_stop(direction, s.structure, entry)

    notes = [f"Entry taken from {entry_source}.", f"ATR stop uses {mult}x ATR for a {s.regime.regime} regime."]

    if struct_stop is None:
        stop = atr_stop
        method = "atr"
        notes.append("No opposing swing available, so the ATR stop stands alone.")
    else:
        # Wider of the two: never place a stop inside noise.
        if direction == "LONG":
            stop = min(atr_stop, struct_stop)
        else:
            stop = max(atr_stop, struct_stop)
        method = "atr" if stop == atr_stop else "structure"
        notes.append("Stop is the wider of the ATR and structural levels.")

    risk = abs(entry - stop)
    if risk <= 0:
        raise RiskRejection("ZERO_RISK", "Stop resolved to the entry price.")
    risk_pct = risk / entry
    if risk_pct > 0.08:
        raise RiskRejection("STOP_TOO_WIDE", f"Stop distance of {risk_pct:.1%} is beyond the risk budget.")
    if risk_pct < 0.0015:
        raise RiskRejection("STOP_TOO_TIGHT", "Stop sits inside the noise band for this market.")

    # Obstacles ahead of the trade, nearest first. The n-th target respects the
    # n-th obstacle: TP1 stops at the first level, TP2 assumes that level breaks
    # and stops at the next, and so on. Capping every target at the *same*
    # nearest level would collapse the whole plan onto one price.
    raw_levels = s.structure.resistance if direction == "LONG" else s.structure.support
    obstacles = sorted(
        (l for l in raw_levels if _beyond(l, entry, direction)),
        key=lambda l: abs(l - entry),
    )
    # A level sitting inside half the stop distance is noise, not resistance.
    # It cannot define a target, but it is worth telling the user about.
    noise_band = 0.5 * risk
    near_obstacle = [l for l in obstacles if abs(l - entry) < noise_band]
    obstacles = [l for l in obstacles if abs(l - entry) >= noise_band]
    if near_obstacle:
        notes.append(
            f"A structural level sits at {near_obstacle[0]:,.2f}, inside the noise band, so it "
            "does not define a target. Expect friction there."
        )

    targets: list[float] = []
    for i, r_mult in enumerate((1.0, 2.0, 3.0)):
        r_target = entry + r_mult * risk if direction == "LONG" else entry - r_mult * risk
        cap = obstacles[i] if i < len(obstacles) else None
        if cap is None:
            target = r_target
        else:
            target = min(r_target, cap) if direction == "LONG" else max(r_target, cap)
        targets.append(float(target))

    targets = _monotonic(targets, direction, entry)
    tp1, tp2, tp3 = targets

    reward = abs(tp2 - entry)
    rr = reward / risk
    if rr < settings.min_rr:
        raise RiskRejection(
            "RR_TOO_LOW",
            f"Risk/reward of 1:{rr:.2f} to TP2 is below the 1:{settings.min_rr} floor.",
        )

    vol_pct = float(s.ta.get("vol_percentile") or 0.5)
    if risk_pct > 0.035 or vol_pct > 0.85:
        category = "high"
    elif risk_pct > 0.018:
        category = "medium"
    else:
        category = "low"
    if strength < 55:
        category = "high" if category == "medium" else category

    expires_at = datetime.now(timezone.utc) + EXPIRY_BY_TF.get(s.timeframe, timedelta(hours=6))
    side = "below" if direction == "LONG" else "above"
    invalidation = (
        f"A {s.timeframe} close {side} {stop:,.2f} invalidates this setup. "
        f"The idea is also void if the signal expires before entry is reached."
    )

    return RiskPlan(
        entry=round(entry, 8),
        stop_loss=round(float(stop), 8),
        tp1=round(tp1, 8),
        tp2=round(tp2, 8),
        tp3=round(tp3, 8),
        risk_reward=round(float(rr), 3),
        risk_category=category,
        expires_at=expires_at,
        invalidation=invalidation,
        stop_method=method,
        notes=notes,
    )


def _beyond(level: float, entry: float, direction: str) -> bool:
    return level > entry if direction == "LONG" else level < entry


def _monotonic(targets: list[float], direction: str, entry: float) -> list[float]:
    """Force TP1 < TP2 < TP3 (or the reverse for shorts) with real separation."""
    out = sorted(targets) if direction == "LONG" else sorted(targets, reverse=True)
    fixed: list[float] = []
    for i, t in enumerate(out):
        if i == 0:
            fixed.append(t)
            continue
        prev = fixed[-1]
        min_gap = abs(entry) * 0.0008
        if direction == "LONG" and t <= prev + min_gap:
            t = prev + min_gap
        elif direction == "SHORT" and t >= prev - min_gap:
            t = prev - min_gap
        fixed.append(t)
    return fixed


# ───────────────────────────── publication gates ────────────────────────────


@dataclass(slots=True)
class GateResult:
    passed: bool
    code: str | None = None
    message: str | None = None


def data_quality_gate(s: FeatureSnapshot) -> GateResult:
    """Refuse to publish on degraded data. The UI shows DATA DELAYED instead.

    A wrong signal costs a user money; a missing signal costs them nothing.
    """
    q = s.quality
    if q.degraded:
        return GateResult(False, "DATA_DEGRADED", "; ".join(q.flags) or "Market data is degraded.")
    if q.venues_live < settings.min_live_venues:
        return GateResult(
            False,
            "INSUFFICIENT_VENUES",
            f"Only {q.venues_live} venue(s) live; {settings.min_live_venues} required for corroboration.",
        )
    if q.max_staleness_ms > settings.max_trade_staleness_ms:
        return GateResult(False, "DATA_STALE", f"Feed is {q.max_staleness_ms} ms behind.")
    if abs(q.clock_skew_ms) > settings.max_clock_skew_ms:
        return GateResult(False, "CLOCK_SKEW", f"Clock skew of {q.clock_skew_ms} ms exceeds tolerance.")
    return GateResult(True)


def liquidity_gate(s: FeatureSnapshot) -> GateResult:
    """Spread is a risk gate and never a directional vote."""
    if not s.micro.available:
        return GateResult(True)
    if s.micro.spread_z > 4.0:
        return GateResult(False, "SPREAD_EXTREME",
                          f"Spread is {s.micro.spread_z:.1f} robust-z above normal; execution would be poor.")
    if s.micro.is_crossed if hasattr(s.micro, "is_crossed") else False:
        return GateResult(False, "BOOK_CROSSED", "The order book is crossed.")
    return GateResult(True)


def strength_gate(strength: int, minimum: int = 45) -> GateResult:
    if strength < minimum:
        return GateResult(False, "BELOW_THRESHOLD",
                          f"Signal strength {strength} is below the publication floor of {minimum}.")
    return GateResult(True)


def run_gates(s: FeatureSnapshot, strength: int) -> GateResult:
    for gate in (data_quality_gate(s), liquidity_gate(s), strength_gate(strength)):
        if not gate.passed:
            return gate
    return GateResult(True)
