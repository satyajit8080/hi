"""Market regime detection and the multi-timeframe hierarchy.

Regime decides *which strategies are allowed to speak*. Mean reversion is muted
in a strong trend; trend-following is muted in a range. That gating is the
cheapest robustness win available and it is fully rule-based, so it can be
explained to a user in one line.

The timeframe hierarchy is the second discipline:

    1d + 4h   direction / permission
    1h        setup confirmation
    15m + 5m  entry trigger

Alignment is computed with the higher timeframes weighted heaviest. A setup that
opposes the daily is not upgraded to a signal just because a 15m candle looks
good — it is demoted to a watch item, exactly as the research specified.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal

import numpy as np

from app.features.indicators import adx, ema, percentile_rank, realized_volatility

Regime = Literal["trend_bull", "trend_bear", "range", "high_volatility"]

# Higher timeframes carry more weight in the alignment score.
TF_WEIGHTS: dict[str, float] = {"1d": 0.35, "4h": 0.28, "1h": 0.20, "15m": 0.11, "5m": 0.06}


@dataclass(slots=True)
class RegimeState:
    regime: Regime
    trend_score: float          # -1 (bearish) .. +1 (bullish)
    adx: float
    vol_percentile: float
    above_200ema: bool
    ema_stack: str              # "bull" | "bear" | "mixed"

    def to_dict(self) -> dict:
        return asdict(self)


def classify_regime(high, low, close, adx_threshold: float = 25.0) -> RegimeState:
    c = np.asarray(close, dtype=float)
    ema20, ema50, ema200 = ema(c, 20), ema(c, 50), ema(c, 200)
    adx_v, _, _ = adx(high, low, close, 14)
    vol = realized_volatility(c, 20)
    vol_pct = percentile_rank(vol, 200)

    last = len(c) - 1
    adx_last = float(adx_v[last]) if not np.isnan(adx_v[last]) else 0.0
    vol_last = float(vol_pct[last]) if not np.isnan(vol_pct[last]) else 0.5

    e20 = float(ema20[last]) if not np.isnan(ema20[last]) else c[last]
    e50 = float(ema50[last]) if not np.isnan(ema50[last]) else c[last]
    e200 = float(ema200[last]) if not np.isnan(ema200[last]) else c[last]

    if e20 > e50 > e200:
        stack = "bull"
    elif e20 < e50 < e200:
        stack = "bear"
    else:
        stack = "mixed"

    slope = 0.0
    if last >= 10 and not np.isnan(ema20[last - 10]) and ema20[last - 10] != 0:
        slope = float((e20 - ema20[last - 10]) / abs(ema20[last - 10]))

    trend_score = float(
        np.clip(
            (1.0 if stack == "bull" else -1.0 if stack == "bear" else 0.0) * 0.5
            + np.clip(slope * 40.0, -0.3, 0.3)
            + (0.2 if c[last] > e200 else -0.2),
            -1.0,
            1.0,
        )
    )

    if vol_last >= 0.92:
        regime: Regime = "high_volatility"
    elif adx_last >= adx_threshold and trend_score > 0.15:
        regime = "trend_bull"
    elif adx_last >= adx_threshold and trend_score < -0.15:
        regime = "trend_bear"
    else:
        regime = "range"

    return RegimeState(
        regime=regime,
        trend_score=trend_score,
        adx=adx_last,
        vol_percentile=vol_last,
        above_200ema=bool(c[last] > e200),
        ema_stack=stack,
    )


@dataclass(slots=True)
class MTFView:
    """Per-timeframe directional bias in [-1, 1]."""

    biases: dict[str, float]
    regimes: dict[str, str]

    def direction_bias(self) -> float:
        """Bias from the direction-setting timeframes only (1d, 4h)."""
        weights = {"1d": 0.6, "4h": 0.4}
        num = sum(self.biases.get(tf, 0.0) * w for tf, w in weights.items())
        den = sum(w for tf, w in weights.items() if tf in self.biases)
        return num / den if den else 0.0

    def alignment(self, direction: str) -> float:
        """Weighted share of the stack agreeing with `direction`, in [0, 1]."""
        sign = 1.0 if direction == "LONG" else -1.0
        total = 0.0
        agree = 0.0
        for tf, bias in self.biases.items():
            w = TF_WEIGHTS.get(tf, 0.05)
            total += w
            if bias * sign > 0.08:
                agree += w
            elif abs(bias) <= 0.08:
                agree += w * 0.4          # neutral is not opposition
        return agree / total if total else 0.0

    def conflicts(self, direction: str) -> list[str]:
        sign = 1.0 if direction == "LONG" else -1.0
        return [tf for tf, bias in self.biases.items() if bias * sign < -0.15]


def build_mtf_view(regimes_by_tf: dict[str, RegimeState]) -> MTFView:
    return MTFView(
        biases={tf: r.trend_score for tf, r in regimes_by_tf.items()},
        regimes={tf: r.regime for tf, r in regimes_by_tf.items()},
    )


def counter_trend(view: MTFView, direction: str, threshold: float = 0.2) -> bool:
    """True when the proposed direction fights the 1d/4h bias."""
    bias = view.direction_bias()
    sign = 1.0 if direction == "LONG" else -1.0
    return bias * sign < -threshold
