"""Six independent strategies and the regime gate that decides who may speak.

Each strategy returns a directional score in [-1, 1] plus its own reason codes.
None of them sees the others. The meta layer in `meta.py` weights whichever ones
the current regime permits.

Deliberately no single "mega strategy": when one model owns everything, a regime
change breaks the whole product at once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from app.features.snapshot import FeatureSnapshot


@dataclass(slots=True)
class StrategyOutput:
    name: str
    score: float                     # -1 .. +1
    confidence: float                # 0 .. 1, how sure this strategy is
    reasons: list[dict] = field(default_factory=list)

    def add(self, code: str, label: str, value, weight: float, sign: str) -> None:
        self.reasons.append(
            {
                "code": code,
                "label": label,
                "value": _clean(value),
                "weight": round(weight, 4),
                "sign": sign,
            }
        )


def _clean(v):
    if isinstance(v, (int, str, bool)) or v is None:
        return v
    try:
        f = float(v)
        return None if np.isnan(f) else round(f, 6)
    except (TypeError, ValueError):
        return str(v)


def _safe(x, default: float = 0.0) -> float:
    try:
        f = float(x)
        return default if np.isnan(f) else f
    except (TypeError, ValueError):
        return default


# ─────────────────────────────── strategies ─────────────────────────────────


def trend_following(s: FeatureSnapshot) -> StrategyOutput:
    out = StrategyOutput("trend_following", 0.0, 0.0)
    ta = s.ta
    price = _safe(ta.get("close"))
    e20, e50, e200 = _safe(ta.get("ema20")), _safe(ta.get("ema50")), _safe(ta.get("ema200"))
    adx_v = _safe(ta.get("adx"))
    score = 0.0

    if e20 and e50 and e200:
        if e20 > e50 > e200:
            score += 0.45
            out.add("EMA_STACK_BULL", "EMA 20 > 50 > 200", "bull", 0.45, "support")
        elif e20 < e50 < e200:
            score -= 0.45
            out.add("EMA_STACK_BEAR", "EMA 20 < 50 < 200", "bear", 0.45, "support")

    if price and e200:
        if price > e200:
            score += 0.2
            out.add("PRICE_ABOVE_200EMA", "Price above the 200 EMA", True, 0.2, "support")
        else:
            score -= 0.2
            out.add("PRICE_BELOW_200EMA", "Price below the 200 EMA", True, 0.2, "support")

    if adx_v >= 25:
        boost = min((adx_v - 25) / 25.0, 1.0) * 0.35
        score += boost * (1 if score > 0 else -1 if score < 0 else 0)
        out.add("ADX_TRENDING", "ADX confirms a trending market", round(adx_v, 1), boost, "support")
    elif adx_v and adx_v < 18:
        score *= 0.5
        out.add("ADX_WEAK", "ADX is low, trend strength is poor", round(adx_v, 1), 0.0, "risk")

    out.score = float(np.clip(score, -1, 1))
    out.confidence = min(abs(out.score) + (0.2 if adx_v >= 25 else 0.0), 1.0)
    return out


def momentum(s: FeatureSnapshot) -> StrategyOutput:
    out = StrategyOutput("momentum", 0.0, 0.0)
    ta = s.ta
    rsi_v, rsi_prev = _safe(ta.get("rsi14"), 50), _safe(ta.get("rsi14_prev"), 50)
    hist, hist_prev = _safe(ta.get("macd_hist")), _safe(ta.get("macd_hist_prev"))
    roc = _safe(ta.get("roc10"))
    score = 0.0

    if rsi_prev < 30 <= rsi_v:
        score += 0.4
        out.add("RSI_EXIT_OVERSOLD", "RSI has climbed back out of oversold", round(rsi_v, 1), 0.4, "support")
    elif rsi_prev > 70 >= rsi_v:
        score -= 0.4
        out.add("RSI_EXIT_OVERBOUGHT", "RSI has dropped out of overbought", round(rsi_v, 1), 0.4, "support")
    elif 45 <= rsi_v <= 65:
        score += 0.12 * np.sign(rsi_v - 50)
        out.add("RSI_NEUTRAL_TILT", "RSI sits in the neutral band", round(rsi_v, 1), 0.12, "support")
    elif rsi_v > 78:
        out.add("RSI_STRETCHED", "RSI is stretched, chase risk", round(rsi_v, 1), 0.0, "risk")
    elif rsi_v < 22:
        out.add("RSI_STRETCHED", "RSI is deeply oversold, knife risk", round(rsi_v, 1), 0.0, "risk")

    if hist > 0 and hist > hist_prev:
        score += 0.3
        out.add("MACD_HIST_RISING", "MACD histogram expanding upward", round(hist, 6), 0.3, "support")
    elif hist < 0 and hist < hist_prev:
        score -= 0.3
        out.add("MACD_HIST_FALLING", "MACD histogram expanding downward", round(hist, 6), 0.3, "support")

    if abs(roc) > 0.4:
        contrib = float(np.clip(roc / 8.0, -0.3, 0.3))
        score += contrib
        out.add("ROC_MOMENTUM", "10-bar rate of change", round(roc, 3), abs(contrib), "support")

    out.score = float(np.clip(score, -1, 1))
    out.confidence = min(abs(out.score), 1.0)
    return out


def mean_reversion(s: FeatureSnapshot) -> StrategyOutput:
    out = StrategyOutput("mean_reversion", 0.0, 0.0)
    ta = s.ta
    price = _safe(ta.get("close"))
    bb_up, bb_mid, bb_low = _safe(ta.get("bb_upper")), _safe(ta.get("bb_mid")), _safe(ta.get("bb_lower"))
    rsi_v = _safe(ta.get("rsi14"), 50)
    score = 0.0

    if price and bb_low and price <= bb_low:
        score += 0.5
        out.add("BB_LOWER_TAG", "Price tagged the lower Bollinger band", round(price, 2), 0.5, "support")
    elif price and bb_up and price >= bb_up:
        score -= 0.5
        out.add("BB_UPPER_TAG", "Price tagged the upper Bollinger band", round(price, 2), 0.5, "support")

    if rsi_v < 30:
        score += 0.35
        out.add("RSI_OVERSOLD", "RSI is oversold", round(rsi_v, 1), 0.35, "support")
    elif rsi_v > 70:
        score -= 0.35
        out.add("RSI_OVERBOUGHT", "RSI is overbought", round(rsi_v, 1), 0.35, "support")

    if price and bb_mid:
        distance = (price - bb_mid) / bb_mid
        if abs(distance) > 0.02:
            contrib = float(np.clip(-distance * 6, -0.25, 0.25))
            score += contrib
            out.add("BB_MEAN_PULL", "Stretched away from the 20-period mean", round(distance, 4), abs(contrib), "support")

    out.score = float(np.clip(score, -1, 1))
    out.confidence = min(abs(out.score), 1.0)
    return out


def breakout(s: FeatureSnapshot) -> StrategyOutput:
    out = StrategyOutput("breakout", 0.0, 0.0)
    st = s.structure
    vol_ratio = _safe(s.ta.get("volume_ratio"), 1.0)
    score = 0.0

    if st.broke_structure and st.break_direction == "up":
        score += 0.5
        out.add("BOS_UP", "Price broke the last swing high", st.last_high, 0.5, "support")
    elif st.broke_structure and st.break_direction == "down":
        score -= 0.5
        out.add("BOS_DOWN", "Price broke the last swing low", st.last_low, 0.5, "support")

    if score != 0:
        if vol_ratio >= 1.4:
            boost = min((vol_ratio - 1.0) * 0.3, 0.35)
            score += boost * np.sign(score)
            out.add("VOL_CONFIRM", "Volume confirms the break", round(vol_ratio, 2), boost, "support")
        elif vol_ratio < 0.9:
            score *= 0.5
            out.add("VOL_THIN", "Break is happening on thin volume", round(vol_ratio, 2), 0.0, "risk")

    out.score = float(np.clip(score, -1, 1))
    out.confidence = min(abs(out.score), 1.0)
    return out


def volatility_expansion(s: FeatureSnapshot) -> StrategyOutput:
    """Squeeze release: low BB width resolving in the direction of the trend."""
    out = StrategyOutput("volatility_expansion", 0.0, 0.0)
    width_pct = _safe(s.ta.get("bb_width_pct"), 0.5)
    trend = s.regime.trend_score
    price, bb_mid = _safe(s.ta.get("close")), _safe(s.ta.get("bb_mid"))
    score = 0.0

    if width_pct <= 0.2:
        direction = np.sign(price - bb_mid) if price and bb_mid else np.sign(trend)
        score = float(np.clip(direction * 0.45, -0.45, 0.45))
        out.add("SQUEEZE_RELEASE", "Bollinger squeeze resolving", round(width_pct, 3), 0.45, "support")
        if abs(trend) > 0.3:
            score += 0.25 * np.sign(trend)
            out.add("SQUEEZE_TREND_ALIGNED", "Squeeze resolving with the prevailing trend", round(trend, 3), 0.25, "support")
    elif width_pct >= 0.9:
        out.add("VOL_EXPANDED", "Volatility already expanded, late entry risk", round(width_pct, 3), 0.0, "risk")

    out.score = float(np.clip(score, -1, 1))
    out.confidence = min(abs(out.score), 1.0)
    return out


def market_structure(s: FeatureSnapshot) -> StrategyOutput:
    out = StrategyOutput("market_structure", 0.0, 0.0)
    st = s.structure
    price = _safe(s.ta.get("close"))
    score = 0.0

    if st.trend == "up":
        score += 0.45
        out.add("STRUCTURE_HH_HL", "Higher highs and higher lows", "up", 0.45, "support")
    elif st.trend == "down":
        score -= 0.45
        out.add("STRUCTURE_LH_LL", "Lower highs and lower lows", "down", 0.45, "support")

    if price and st.resistance:
        dist = (min(st.resistance) - price) / price
        if 0 < dist < 0.006:
            score -= 0.2
            out.add("RESISTANCE_NEARBY", "Resistance sits just overhead", round(dist * 100, 3), 0.2, "risk")
    if price and st.support:
        dist = (price - max(st.support)) / price
        if 0 < dist < 0.006:
            score += 0.2
            out.add("SUPPORT_NEARBY", "Support sits just below", round(dist * 100, 3), 0.2, "support")

    out.score = float(np.clip(score, -1, 1))
    out.confidence = min(abs(out.score), 1.0)
    return out


STRATEGIES: dict[str, Callable[[FeatureSnapshot], StrategyOutput]] = {
    "trend_following": trend_following,
    "momentum": momentum,
    "mean_reversion": mean_reversion,
    "breakout": breakout,
    "volatility_expansion": volatility_expansion,
    "market_structure": market_structure,
}

# Which strategies are permitted in which regime, and at what weight.
# Mean reversion is off in a trend; trend following is off in a range. High
# volatility mutes everything and leans on structure, because that is the regime
# where indicator-driven entries fail most expensively.
REGIME_GATES: dict[str, dict[str, float]] = {
    "trend_bull": {
        "trend_following": 1.0,
        "momentum": 0.9,
        "breakout": 0.8,
        "market_structure": 0.7,
        "volatility_expansion": 0.5,
        "mean_reversion": 0.0,
    },
    "trend_bear": {
        "trend_following": 1.0,
        "momentum": 0.9,
        "breakout": 0.8,
        "market_structure": 0.7,
        "volatility_expansion": 0.5,
        "mean_reversion": 0.0,
    },
    "range": {
        "mean_reversion": 1.0,
        "market_structure": 0.8,
        "volatility_expansion": 0.6,
        "momentum": 0.4,
        "breakout": 0.3,
        "trend_following": 0.0,
    },
    "high_volatility": {
        "market_structure": 0.6,
        "breakout": 0.4,
        "trend_following": 0.3,
        "momentum": 0.3,
        "volatility_expansion": 0.2,
        "mean_reversion": 0.0,
    },
}


def run_strategies(s: FeatureSnapshot) -> dict[str, StrategyOutput]:
    return {name: fn(s) for name, fn in STRATEGIES.items()}


def gate_for(regime: str) -> dict[str, float]:
    return REGIME_GATES.get(regime, REGIME_GATES["range"])
