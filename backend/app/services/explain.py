"""Natural-language explanation of engine output.

The quantitative engine is the only thing that decides. This module turns its
structured output — reason codes, risk flags, classification, calibration — into
prose, deterministically, from templates. An optional LLM hook exists for richer
summaries, but it only ever receives the same structured record and is never
asked what to do; its text is labelled as generated and carries no numbers the
engine did not produce.
"""
from __future__ import annotations

from typing import Any

from app.config import settings

CLASS_TEXT = {
    "STRONG_LONG": "a strong long",
    "WEAK_LONG": "a weak long",
    "STRONG_SHORT": "a strong short",
    "WEAK_SHORT": "a weak short",
    "NO_TRADE": "no trade",
}


def explain_signal(signal: dict[str, Any]) -> dict[str, Any]:
    features = signal.get("features") or {}
    signal_class = features.get("signal_class") or (
        ("STRONG_" if signal["strength"] >= 70 else "WEAK_") + signal["direction"]
    )
    drivers = [r for r in signal.get("reason_codes", []) if r.get("role") == "driver"]
    context = [r for r in signal.get("reason_codes", []) if r.get("role") == "context_only"]
    risks = signal.get("risk_flags", [])
    supporting = [r for r in drivers if r["contribution"] > 0][:4]
    opposing = [r for r in drivers if r["contribution"] < 0][:2]

    parts = [
        f"The engine classified {signal['symbol'].replace('USDT', '')} on the {signal['timeframe']} "
        f"chart as {CLASS_TEXT.get(signal_class, signal_class.lower())} with a strength of "
        f"{signal['strength']} out of 100, in a {signal['regime'].replace('_', ' ')} regime."
    ]
    if supporting:
        parts.append("The case for it rests on " + _join([r["label"].lower() for r in supporting]) + ".")
    if opposing:
        parts.append("Working against it: " + _join([r["label"].lower() for r in opposing]) + ".")
    if risks:
        parts.append("Risks the engine can see: " + _join([r["label"].lower() for r in risks[:3]]) + ".")

    wr = signal.get("calibrated_winrate")
    n = signal.get("calibration_sample")
    if wr is not None:
        parts.append(
            f"Signals of similar strength have closed as wins {wr:.0%} of the time across {n} closed "
            f"signals. That is a historical rate, not a forecast."
        )
    else:
        parts.append(
            "Too few signals of similar strength have closed to quote a historical win rate, so none is shown."
        )
    if context:
        parts.append(
            "On-chain and whale readings are shown alongside for context; they carried zero weight in "
            "this score."
        )
    parts.append(
        f"The plan is entry {signal['entry']}, stop {signal['stop_loss']}, targets {signal['tp1']}, "
        f"{signal['tp2']} and {signal['tp3']}, for a reward-to-risk of 1:{signal['risk_reward']}. "
        f"{signal['invalidation']}"
        if signal.get("entry") is not None else
        "Entry, stop and targets are withheld on the free plan until the delay elapses."
    )

    return {
        "signal_id": signal["signal_id"],
        "signal_class": signal_class,
        "summary": " ".join(parts),
        "supporting": [r["label"] for r in supporting],
        "opposing": [r["label"] for r in opposing],
        "risks": [r["label"] for r in risks],
        "context_only": [r["label"] for r in context],
        "source": "deterministic_template",
        "disclaimer": (
            "Generated from the engine's structured output. The explanation never adds or changes a "
            "decision; the numbers are the engine's, not the writer's."
        ),
    }


def explain_market(intel: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for a in intel.get("assets", []):
        comps = a.get("components", {})
        weakest = min(comps, key=comps.get) if comps else None
        lines.append(
            f"{a['symbol'].replace('USDT', '')}: {a['regime'].replace('_', ' ')}, conditions score "
            f"{a['score']}/100" + (f", held back mostly by {weakest.replace('_', ' ')}." if weakest else ".")
        )
    return {
        "summary": " ".join(lines) or "No market intelligence has been computed yet.",
        "overall_conditions_score": intel.get("overall_conditions_score"),
        "source": "deterministic_template",
        "weights": intel.get("weights"),
    }


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def llm_enabled() -> bool:
    """An LLM summary layer is optional and off unless configured. Even when on,
    it receives only the structured record produced here."""
    return bool(getattr(settings, "llm_api_key", ""))
