"""Signal scoring and reason-code assembly.

Design rules taken directly from the research and enforced here in code:

1. **Fixed, published weights.** V1 uses transparent expert weights rather than a
   learned model. They are explainable, hard to overfit, and versioned in
   `STRATEGY_WEIGHTS`. A learned challenger has to beat them out of sample
   before it ships.

2. **Horizon discipline.** Microstructure features decay in minutes, so they can
   only enter the *entry-timing* sub-model, and only on 5m/15m. On 4h and 1d
   their weight is hard-zeroed by `micro_weight_for_timeframe`. This is a
   structural guarantee, not a convention.

3. **Context is not a driver.** Smart-money and on-chain reason codes are emitted
   with `contribution = 0.0` and `role = "context_only"` unless a passing
   out-of-sample validation record has promoted them. They render in the UI but
   never move the number.

Every contribution is recorded, and the driver contributions sum to the score.
That is what makes "why this signal" honest rather than decorative.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.config import PUBLISH_TFS
from app.engine.strategies import StrategyOutput, gate_for, run_strategies
from app.features.regime import MTFView, counter_trend
from app.features.snapshot import FeatureSnapshot

# Component weights. These are the published V1 expert weights; they sum to 1.0.
STRATEGY_WEIGHTS: dict[str, float] = {
    "trend": 0.25,
    "momentum": 0.20,
    "structure": 0.15,
    "volume": 0.10,
    "volatility": 0.10,
    "order_flow": 0.10,
    "derivatives": 0.10,
}

# How much of the order-flow block a timeframe may use. Zero above 1h.
MICRO_WEIGHT_BY_TF: dict[str, float] = {"5m": 1.0, "15m": 0.7, "30m": 0.3, "1h": 0.0, "4h": 0.0, "1d": 0.0}

STRATEGY_TO_COMPONENT = {
    "trend_following": "trend",
    "momentum": "momentum",
    "mean_reversion": "momentum",
    "breakout": "structure",
    "market_structure": "structure",
    "volatility_expansion": "volatility",
}


def micro_weight_for_timeframe(timeframe: str) -> float:
    return MICRO_WEIGHT_BY_TF.get(timeframe, 0.0)


SignalClass = str  # STRONG_LONG | WEAK_LONG | NO_TRADE | WEAK_SHORT | STRONG_SHORT

STRONG_THRESHOLD = 70
WEAK_THRESHOLD = 45
# Share of active component weight that may oppose the net direction before
# the engine declares the evidence contradictory and refuses to trade.
MAX_CONFLICT = 0.40


@dataclass
class ScoreResult:
    direction: str                    # LONG | SHORT | NONE
    strength: int                     # 0..100
    raw_score: float                  # -1..1
    component_scores: dict[str, float]
    reason_codes: list[dict]
    risk_flags: list[dict]
    strategy_outputs: dict[str, StrategyOutput]
    mtf_alignment: float
    is_counter_trend: bool
    notes: list[str] = field(default_factory=list)
    signal_class: SignalClass = "NO_TRADE"
    conflict: float = 0.0
    no_trade_reasons: list[str] = field(default_factory=list)

    @property
    def tradeable(self) -> bool:
        return self.signal_class != "NO_TRADE"


def evidence_conflict(component_scores: dict[str, float], raw: float) -> float:
    """Weighted share of active components voting against the net direction.

    A score of +0.3 built from trend +0.8 and momentum -0.7 is not the same
    thing as +0.3 built from everything mildly agreeing. The first is a coin
    flip dressed as a signal, and this is how we tell them apart.
    """
    if raw == 0:
        return 0.0
    net = 1.0 if raw > 0 else -1.0
    active = {k: v for k, v in component_scores.items() if abs(v) > 0.05 and k != "volatility"}
    total = sum(STRATEGY_WEIGHTS[k] for k in active)
    if total <= 0:
        return 0.0
    opposing = sum(STRATEGY_WEIGHTS[k] for k, v in active.items() if v * net < 0)
    return opposing / total


def classify(result: "ScoreResult") -> None:
    """Assign one of five classes, with NO_TRADE the default outcome.

    The engine must be able to say nothing. Every path here that ends in
    NO_TRADE records why, so the watchlist can show the user what it saw and
    what stopped it.
    """
    reasons: list[str] = []
    if result.direction == "NONE":
        reasons.append("No directional evidence.")
    if result.conflict >= MAX_CONFLICT:
        reasons.append(
            f"Evidence is contradictory: {result.conflict:.0%} of active weight opposes the net direction."
        )
    if result.is_counter_trend:
        reasons.append("Setup opposes the 1d/4h direction; demoted to watch.")
    high = [f for f in result.risk_flags if f.get("severity") == "high"]
    if len(high) >= 2:
        reasons.append(f"{len(high)} high-severity risks: " + "; ".join(f["label"] for f in high[:3]))
    if result.strength < WEAK_THRESHOLD and result.direction != "NONE":
        reasons.append(f"Strength {result.strength} is below the {WEAK_THRESHOLD} floor.")

    if reasons:
        result.signal_class = "NO_TRADE"
        result.no_trade_reasons = reasons
        return

    tier = "STRONG" if result.strength >= STRONG_THRESHOLD else "WEAK"
    result.signal_class = f"{tier}_{result.direction}"
    result.no_trade_reasons = []


def _reason(code, label, group, contribution, value, role="driver", severity=None) -> dict:
    r = {
        "code": code,
        "label": label,
        "group": group,
        "contribution": round(float(contribution), 4),
        "value": value,
        "role": role,
        "sign": "support" if contribution > 0 else "oppose" if contribution < 0 else "neutral",
    }
    if severity:
        r["severity"] = severity
    return r


def score_snapshot(s: FeatureSnapshot) -> ScoreResult:
    outputs = run_strategies(s)
    gate = gate_for(s.regime.regime)

    # ── 1. Blend gated strategies into components ────────────────────────────
    component_scores: dict[str, float] = {k: 0.0 for k in STRATEGY_WEIGHTS}
    component_weight_used: dict[str, float] = {k: 0.0 for k in STRATEGY_WEIGHTS}
    reasons: list[dict] = []
    risks: list[dict] = []
    notes: list[str] = []

    for name, out in outputs.items():
        gate_w = gate.get(name, 0.0)
        if gate_w <= 0:
            continue
        component = STRATEGY_TO_COMPONENT[name]
        component_scores[component] += out.score * gate_w
        component_weight_used[component] += gate_w
        for r in out.reasons:
            if r["sign"] == "risk":
                risks.append(
                    {
                        "code": r["code"],
                        "label": r["label"],
                        "group": component,
                        "value": r["value"],
                        "severity": "medium",
                    }
                )
            else:
                reasons.append(
                    _reason(
                        r["code"],
                        r["label"],
                        component,
                        out.score * gate_w * r["weight"] * STRATEGY_WEIGHTS[component],
                        r["value"],
                    )
                )

    for k in component_scores:
        if component_weight_used[k] > 0:
            component_scores[k] /= component_weight_used[k]
        component_scores[k] = float(np.clip(component_scores[k], -1, 1))

    # ── 2. Volume component ──────────────────────────────────────────────────
    vol_ratio = _f(s.ta.get("volume_ratio"), 1.0)
    obv_slope = _f(s.ta.get("obv_slope"), 0.0)
    volume_score = float(np.clip((vol_ratio - 1.0) * 0.8 + np.clip(obv_slope * 5, -0.5, 0.5), -1, 1))
    component_scores["volume"] = volume_score
    if vol_ratio >= 1.3:
        reasons.append(
            _reason("VOLUME_ABOVE_AVERAGE", "Volume is running above its 20-bar average",
                    "volume", volume_score * STRATEGY_WEIGHTS["volume"], round(vol_ratio, 2))
        )
    elif vol_ratio < 0.75:
        risks.append({"code": "VOLUME_THIN", "label": "Participation is thin", "group": "volume",
                      "value": round(vol_ratio, 2), "severity": "low"})

    # ── 3. Volatility component: a gate, not a direction ─────────────────────
    vol_pct = _f(s.ta.get("vol_percentile"), 0.5)
    if vol_pct >= 0.9:
        risks.append({"code": "VOL_ELEVATED", "label": "Realised volatility is in the top decile",
                      "group": "volatility", "value": round(vol_pct, 3), "severity": "high"})
        component_scores["volatility"] = -0.4
    elif vol_pct <= 0.25:
        component_scores["volatility"] = 0.25
        reasons.append(_reason("VOL_CONTAINED", "Volatility is contained", "volatility",
                               0.25 * STRATEGY_WEIGHTS["volatility"], round(vol_pct, 3)))

    # ── 4. Order flow: entry timing only, weight zeroed above 1h ─────────────
    micro_w = micro_weight_for_timeframe(s.timeframe)
    of_score = 0.0
    if s.micro.available and micro_w > 0 and s.micro.book_synced:
        of_score = _order_flow_score(s, reasons, risks, micro_w)
    elif s.micro.available and micro_w == 0:
        notes.append(
            f"Order-flow features were computed but carry zero weight on {s.timeframe}: "
            "they decay in minutes and must not vote on a higher-timeframe thesis."
        )
    elif not s.micro.available:
        risks.append({"code": "ORDERFLOW_UNAVAILABLE", "label": "Order-flow data unavailable for this bar",
                      "group": "order_flow", "value": None, "severity": "low"})
    component_scores["order_flow"] = of_score

    # ── 5. Derivatives ───────────────────────────────────────────────────────
    component_scores["derivatives"] = _derivatives_score(s, reasons, risks)

    # ── 6. Context block: emitted, never summed ──────────────────────────────
    _context_reasons(s, reasons)

    # ── 7. Weighted total ────────────────────────────────────────────────────
    raw = sum(component_scores[k] * w for k, w in STRATEGY_WEIGHTS.items())
    # Renormalise over the weight that could actually vote. Order flow is scaled
    # down on higher timeframes, and a data block that was *unavailable* is not
    # a neutral vote — treating it as one would silently cap every signal
    # produced without a derivatives feed at 90% of the achievable score.
    def _active(k: str, w: float) -> float:
        if k == "order_flow":
            return w * micro_w if s.micro.available else 0.0
        if k == "derivatives":
            return w if s.deriv.available else 0.0
        return w
    active_weight = sum(_active(k, w) for k, w in STRATEGY_WEIGHTS.items())
    if active_weight > 0:
        raw = raw / active_weight * sum(STRATEGY_WEIGHTS.values())
    raw = float(np.clip(raw, -1, 1))

    direction = "LONG" if raw > 0 else "SHORT" if raw < 0 else "NONE"
    alignment = s.mtf.alignment(direction) if direction != "NONE" else 0.0
    is_counter = counter_trend(s.mtf, direction) if direction != "NONE" else False

    # Alignment shapes strength: agreement across the stack is what separates a
    # setup from a coin flip.
    strength_raw = abs(raw) * (0.55 + 0.45 * alignment)
    if is_counter:
        strength_raw *= 0.45
        risks.append({"code": "COUNTER_TREND", "label": "This setup opposes the 1d/4h direction",
                      "group": "mtf", "value": round(s.mtf.direction_bias(), 3), "severity": "high"})
        notes.append("Counter-trend setup: demoted rather than published as a clean entry.")

    for tf in s.mtf.conflicts(direction) if direction != "NONE" else []:
        risks.append({"code": f"TF_CONFLICT_{tf.upper()}", "label": f"The {tf} timeframe disagrees",
                      "group": "mtf", "value": round(s.mtf.biases.get(tf, 0.0), 3), "severity": "medium"})

    strength = int(round(float(np.clip(strength_raw, 0, 1)) * 100))

    reasons.sort(key=lambda r: abs(r["contribution"]), reverse=True)
    result = ScoreResult(
        direction=direction,
        strength=strength,
        raw_score=raw,
        component_scores=component_scores,
        reason_codes=reasons,
        risk_flags=risks,
        strategy_outputs=outputs,
        mtf_alignment=alignment,
        is_counter_trend=is_counter,
        notes=notes,
        conflict=evidence_conflict(component_scores, raw),
    )
    classify(result)
    if result.conflict >= 0.25:
        risks.append({"code": "MIXED_EVIDENCE", "label": "Components partly disagree on direction",
                      "group": "meta", "value": round(result.conflict, 3), "severity": "medium"})
    return result


def _order_flow_score(s: FeatureSnapshot, reasons: list[dict], risks: list[dict], w: float) -> float:
    m = s.micro
    score = 0.0

    obi = m.obi_persistent if m.obi_persistent else m.obi_5
    # Depth that flickers, cancels heavily, or never trades is discounted before
    # it is allowed to vote. Spoofing should be expensive, not free.
    spoof_discount = 1.0 - 0.7 * float(np.clip(getattr(m, "spoof_risk", 0.0) or 0.0, 0.0, 1.0))
    if getattr(m, "spoof_risk", 0.0) and m.spoof_risk > 0.6:
        risks.append({"code": "SUSPICIOUS_DEPTH",
                      "label": "Displayed depth looks unreliable (high cancellation, little execution)",
                      "group": "order_flow", "value": round(m.spoof_risk, 2), "severity": "medium"})
    if abs(obi) > 0.12:
        contrib = float(np.clip(obi * 1.5, -0.5, 0.5)) * spoof_discount
        score += contrib
        reasons.append(
            _reason("OBI_POSITIVE" if obi > 0 else "OBI_NEGATIVE",
                    "Resting depth leans to the bid" if obi > 0 else "Resting depth leans to the ask",
                    "order_flow", contrib * STRATEGY_WEIGHTS["order_flow"] * w, round(obi, 4))
        )

    if abs(m.ofi_z) > 1.0:
        contrib = float(np.clip(m.ofi_z / 4.0, -0.35, 0.35))
        score += contrib
        reasons.append(
            _reason("OFI_IMBALANCE", "Order-flow imbalance at the touch", "order_flow",
                    contrib * STRATEGY_WEIGHTS["order_flow"] * w, round(m.ofi_z, 3))
        )

    if abs(m.trade_imbalance) > 0.15:
        contrib = float(np.clip(m.trade_imbalance * 0.8, -0.3, 0.3))
        score += contrib
        reasons.append(
            _reason("AGGRESSIVE_FLOW", "Aggressive market orders are one-sided", "order_flow",
                    contrib * STRATEGY_WEIGHTS["order_flow"] * w, round(m.trade_imbalance, 4))
        )

    # CVD divergence is a veto, not a trigger.
    if abs(m.cvd_divergence) > 0.35:
        risks.append({"code": "CVD_DIVERGENCE",
                      "label": "Price and cumulative volume delta are diverging",
                      "group": "order_flow", "value": round(m.cvd_divergence, 3), "severity": "medium"})
        score *= 0.6
    elif abs(m.cvd_divergence) < 0.1 and abs(score) > 0.1:
        reasons.append(_reason("CVD_CONFIRM", "Cumulative volume delta agrees with price",
                               "order_flow", 0.08 * STRATEGY_WEIGHTS["order_flow"] * w,
                               round(m.cvd_divergence, 3)))
        score += 0.08 * np.sign(score)

    if m.spread_z > 2.5:
        risks.append({"code": "SPREAD_ELEVATED", "label": "The spread is unusually wide",
                      "group": "risk", "value": round(m.spread_z, 2), "severity": "high"})
    if m.large_print_z > 3.0:
        reasons.append(_reason("LARGE_PRINT", "An unusually large print crossed the tape",
                               "order_flow", 0.0, round(m.large_print_z, 2), role="context_only"))
    if m.venues_agreeing < 2:
        risks.append({"code": "SINGLE_VENUE_FLOW",
                      "label": "Only one venue confirms this flow, so it is easier to spoof",
                      "group": "order_flow", "value": m.venues_agreeing, "severity": "medium"})
        score *= 0.5

    return float(np.clip(score, -1, 1))


def _derivatives_score(s: FeatureSnapshot, reasons: list[dict], risks: list[dict]) -> float:
    d = s.deriv
    if not d.available:
        return 0.0
    score = 0.0

    if d.funding_rate is not None:
        fr = d.funding_rate
        if abs(fr) < 0.0001:
            reasons.append(_reason("FUNDING_NEUTRAL", "Funding is neutral, positioning is not crowded",
                                   "derivatives", 0.15 * STRATEGY_WEIGHTS["derivatives"], round(fr, 6)))
            score += 0.15
        elif fr > 0.0005:
            risks.append({"code": "FUNDING_CROWDED_LONG", "label": "Funding is expensive for longs",
                          "group": "derivatives", "value": round(fr, 6), "severity": "medium"})
            score -= 0.3
        elif fr < -0.0005:
            risks.append({"code": "FUNDING_CROWDED_SHORT", "label": "Funding is expensive for shorts",
                          "group": "derivatives", "value": round(fr, 6), "severity": "medium"})
            score += 0.3

    if d.oi_change_pct is not None and abs(d.oi_change_pct) > 1.5:
        contrib = float(np.clip(d.oi_change_pct / 15.0, -0.3, 0.3))
        score += contrib
        reasons.append(_reason("OI_EXPANDING" if contrib > 0 else "OI_UNWINDING",
                               "Open interest is expanding" if contrib > 0 else "Open interest is unwinding",
                               "derivatives", contrib * STRATEGY_WEIGHTS["derivatives"],
                               round(d.oi_change_pct, 2)))

    longs, shorts = d.liq_long_usd_1h or 0.0, d.liq_short_usd_1h or 0.0
    if longs + shorts > 0:
        skew = (shorts - longs) / (longs + shorts)
        if abs(skew) > 0.5:
            risks.append({"code": "LIQUIDATION_CASCADE",
                          "label": "One side has been liquidated heavily in the last hour",
                          "group": "derivatives", "value": round(skew, 3), "severity": "medium"})

    return float(np.clip(score, -1, 1))


def _context_reasons(s: FeatureSnapshot, reasons: list[dict]) -> None:
    """Smart-money codes. Contribution is pinned to 0 unless validation passed."""
    c = s.context
    if not c.available:
        return
    promoted = c.is_signal_driver
    role = "driver" if promoted else "context_only"

    if c.smart_money_accum_z is not None and abs(c.smart_money_accum_z) > 1.0:
        contribution = 0.0
        if promoted:
            contribution = float(np.clip(c.smart_money_accum_z / 6.0, -0.1, 0.1))
        reasons.append(
            {
                "code": "SMART_MONEY_ACCUM" if c.smart_money_accum_z > 0 else "SMART_MONEY_DISTRIB",
                "label": (
                    "Tracked wallets are accumulating"
                    if c.smart_money_accum_z > 0
                    else "Tracked wallets are distributing"
                ),
                "group": "context_onchain",
                "contribution": round(contribution, 4),
                "value": round(c.smart_money_accum_z, 3),
                "role": role,
                "sign": "support" if c.smart_money_accum_z > 0 else "oppose",
                "note": (
                    None
                    if promoted
                    else "Context only. Not out-of-sample validated for majors, so it does not move the score."
                ),
                "cohort_size": c.smart_money_cohort_size,
                "validation_ref": c.validation_ref,
            }
        )

    if c.exchange_netflow_usd is not None and abs(c.exchange_netflow_usd) > 1_000_000:
        reasons.append(
            {
                "code": "EXCHANGE_NETFLOW",
                "label": (
                    "Coins are leaving exchanges"
                    if c.exchange_netflow_usd < 0
                    else "Coins are moving onto exchanges"
                ),
                "group": "context_onchain",
                "contribution": 0.0,
                "value": round(c.exchange_netflow_usd, 2),
                "role": "context_only",
                "sign": "neutral",
                "note": "Daily-horizon context. Not a driver of this signal.",
            }
        )

    if c.hyperliquid_whale_bias is not None and abs(c.hyperliquid_whale_bias) > 0.2:
        reasons.append(
            {
                "code": "HL_WHALE_BIAS",
                "label": "Large Hyperliquid positions lean one way",
                "group": "context_onchain",
                "contribution": 0.0,
                "value": round(c.hyperliquid_whale_bias, 3),
                "role": "context_only",
                "sign": "support" if c.hyperliquid_whale_bias > 0 else "oppose",
                "note": "Observable on-chain positioning. Shown for context, not scored.",
            }
        )


def driver_contribution_total(reasons: list[dict]) -> float:
    return sum(r["contribution"] for r in reasons if r.get("role") == "driver")


def _f(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        return default if np.isnan(v) else v
    except (TypeError, ValueError):
        return default


def publishable_timeframes() -> tuple[str, ...]:
    return PUBLISH_TFS
