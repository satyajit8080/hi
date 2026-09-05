"""Market intelligence: one screen that says what kind of market this is.

The Market Conditions Score is a weighted blend of eight normalised components.
Every weight is published alongside the score, and every component is shown
with its raw value, so the number can be recomputed by hand from the same page.
It describes conditions for systematic trading, not direction: a clean, liquid,
trending market scores high whether it is trending up or down.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

CONDITION_WEIGHTS: dict[str, float] = {
    "trend_clarity": 0.22,     # |trend score| from the 4h regime
    "regime_stability": 0.13,  # agreement of regimes across timeframes
    "momentum": 0.10,          # |RSI - 50| / 50 plus MACD agreement
    "volatility": 0.15,        # inverted: penalises top-decile realised vol
    "liquidity": 0.15,         # inverted spread z
    "order_flow": 0.10,        # persistent OBI magnitude and CVD agreement
    "derivatives": 0.10,       # funding not crowded, OI not unwinding
    "data_quality": 0.05,      # venues live, freshness
}


@dataclass
class AssetIntel:
    symbol: str
    price: float | None
    trend_score: float
    regime: str
    regimes_by_tf: dict[str, str]
    rsi: float | None
    macd_hist: float | None
    vol_percentile: float | None
    spread_z: float | None
    obi_persistent: float | None
    cvd_divergence: float | None
    funding_rate: float | None
    oi_change_pct: float | None
    liq_long_usd: float
    liq_short_usd: float
    smart_money_z: float | None
    venues_live: int
    staleness_ms: int
    components: dict[str, float] = field(default_factory=dict)
    score: int = 0

    def to_dict(self) -> dict:
        return {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in self.__dict__.items()}


def _f(x, default=None):
    try:
        v = float(x)
        return default if not np.isfinite(v) else v
    except (TypeError, ValueError):
        return default


def condition_components(a: AssetIntel) -> dict[str, float]:
    """Each component is scaled to 0..1 where 1 is favourable for systematic trading."""
    trend_clarity = float(np.clip(abs(a.trend_score), 0, 1))

    regimes = list(a.regimes_by_tf.values())
    agree = max(regimes.count(r) for r in set(regimes)) / len(regimes) if regimes else 0.0
    regime_stability = float(np.clip((agree - 0.34) / 0.66, 0, 1))

    rsi = _f(a.rsi, 50.0)
    macd = _f(a.macd_hist, 0.0)
    rsi_term = min(abs(rsi - 50.0) / 30.0, 1.0)
    macd_agrees = 1.0 if (macd == 0 or np.sign(macd) == np.sign(rsi - 50.0)) else 0.4
    momentum = float(np.clip(0.6 * rsi_term + 0.4 * macd_agrees, 0, 1))

    vol_pct = _f(a.vol_percentile, 0.5)
    volatility = float(np.clip(1.0 - max(0.0, vol_pct - 0.5) * 2.0, 0, 1))

    spread_z = _f(a.spread_z, 0.0)
    liquidity = float(np.clip(1.0 - max(0.0, spread_z) / 4.0, 0, 1))

    obi = abs(_f(a.obi_persistent, 0.0))
    div = abs(_f(a.cvd_divergence, 0.0))
    order_flow = float(np.clip(0.5 * min(obi / 0.4, 1.0) + 0.5 * (1.0 - min(div / 0.6, 1.0)), 0, 1))

    fr = abs(_f(a.funding_rate, 0.0))
    oi = _f(a.oi_change_pct, 0.0)
    derivatives = float(np.clip(1.0 - min(fr / 0.001, 1.0) * 0.6 - (0.4 if oi < -5 else 0.0), 0, 1))

    data_quality = float(np.clip(min(a.venues_live / 2.0, 1.0) * (1.0 if a.staleness_ms < 5000 else 0.5), 0, 1))

    return {
        "trend_clarity": round(trend_clarity, 4),
        "regime_stability": round(regime_stability, 4),
        "momentum": round(momentum, 4),
        "volatility": round(volatility, 4),
        "liquidity": round(liquidity, 4),
        "order_flow": round(order_flow, 4),
        "derivatives": round(derivatives, 4),
        "data_quality": round(data_quality, 4),
    }


def conditions_score(components: dict[str, float]) -> int:
    total = sum(CONDITION_WEIGHTS[k] * components.get(k, 0.0) for k in CONDITION_WEIGHTS)
    return int(round(float(np.clip(total, 0, 1)) * 100))


def correlation_matrix(returns: dict[str, list[float]], window: int = 48) -> dict[str, dict[str, float | None]]:
    symbols = list(returns)
    out: dict[str, dict[str, float | None]] = {s: {} for s in symbols}
    for a in symbols:
        for b in symbols:
            ra = np.asarray(returns[a][-window:], dtype=float)
            rb = np.asarray(returns[b][-window:], dtype=float)
            n = min(len(ra), len(rb))
            if n < 10:
                out[a][b] = None
                continue
            ra, rb = ra[-n:], rb[-n:]
            if ra.std() == 0 or rb.std() == 0:
                out[a][b] = None
            else:
                out[a][b] = round(float(np.corrcoef(ra, rb)[0, 1]), 3)
    return out


def build_report(assets: list[AssetIntel], returns: dict[str, list[float]], generated_at: str,
                 is_simulated: bool) -> dict:
    for a in assets:
        a.components = condition_components(a)
        a.score = conditions_score(a.components)
    overall = int(round(np.mean([a.score for a in assets]))) if assets else 0
    return {
        "generated_at": generated_at,
        "is_simulated": is_simulated,
        "overall_conditions_score": overall,
        "assets": [a.to_dict() for a in assets],
        "correlation": correlation_matrix(returns),
        "weights": CONDITION_WEIGHTS,
        "explanation": (
            "Each component is scaled 0-1 where 1 is favourable for systematic trading, then blended "
            "with the published weights and scaled to 0-100. The score describes conditions, not "
            "direction: a liquid, clearly trending market scores high whether it is rising or falling. "
            "Smart-money and liquidation figures are shown for context and carry no weight."
        ),
    }
