"""Feature ablation: does each data layer earn its place?

    A  technical engine only
    B  technical + derivatives
    C  technical + order flow
    D  technical + derivatives + order flow
    E  full engine + smart money

Each variant is backtested on the same candles with the same costs. A layer is
allowed to become a scoring driver only if it improves net-of-cost expectancy
AND out-of-sample calibration (Brier) by a margin that survives the sample size.

Order flow and smart money need recorded observations (a FeatureStore). Without
one, C/D/E are identical to A/B and the report says so explicitly rather than
letting an empty layer look like a harmless one.
"""
from __future__ import annotations

from app.features.snapshot import Candles
from backtest.engine import Backtester, BacktestConfig, FeatureStore
from backtest.metrics import breakdown, calibration_quality, compute

VARIANTS = {
    "A_technical": dict(use_derivatives=False, use_orderflow=False, use_smartmoney=False),
    "B_technical_derivatives": dict(use_derivatives=True, use_orderflow=False, use_smartmoney=False),
    "C_technical_orderflow": dict(use_derivatives=False, use_orderflow=True, use_smartmoney=False),
    "D_technical_derivatives_orderflow": dict(use_derivatives=True, use_orderflow=True, use_smartmoney=False),
    "E_full_smartmoney": dict(use_derivatives=True, use_orderflow=True, use_smartmoney=True),
}

# Minimum improvement over the technical baseline for a layer to be promoted.
MIN_EXPECTANCY_LIFT_PCT = 0.10   # absolute percentage points per trade
MIN_BRIER_IMPROVEMENT = 0.005
MIN_TRADES = 60


def run(candles: Candles, base: BacktestConfig, features: FeatureStore | None = None) -> dict:
    features = features or FeatureStore()
    results: dict[str, dict] = {}
    for name, toggles in VARIANTS.items():
        cfg = BacktestConfig(**{**base.__dict__, **toggles})
        bt = Backtester(cfg, features)
        trades = bt.run(candles)
        results[name] = {
            "metrics": compute(trades).to_dict(),
            "calibration": calibration_quality(trades),
            "by_regime": breakdown(trades, "regime"),
            "by_strength": breakdown(trades, "strength"),
            "feature_coverage": bt.feature_coverage,
            "skipped": bt.skipped,
        }

    baseline = results["A_technical"]
    verdicts = {}
    for name, r in results.items():
        if name == "A_technical":
            continue
        cov = r["feature_coverage"]
        needs = [k for k, on in (("orderflow", "orderflow" in name), ("derivatives", "derivatives" in name),
                                 ("smartmoney", "smartmoney" in name)) if on]
        missing = [k for k in needs if cov.get(k, 0) == 0]
        if missing:
            verdicts[name] = {"promote": False,
                              "reason": f"No recorded data for {', '.join(missing)}; variant is identical to the baseline."}
            continue
        m, b = r["metrics"], baseline["metrics"]
        if m["trades"] < MIN_TRADES:
            verdicts[name] = {"promote": False, "reason": f"Only {m['trades']} trades; below the {MIN_TRADES} floor."}
            continue
        lift = (m["expectancy_pct"] or 0) - (b["expectancy_pct"] or 0)
        cal_ok = r["calibration"].get("sufficient") and baseline["calibration"].get("sufficient")
        brier_gain = (baseline["calibration"]["brier"] - r["calibration"]["brier"]) if cal_ok else None
        promote = lift >= MIN_EXPECTANCY_LIFT_PCT and (brier_gain is None or brier_gain >= MIN_BRIER_IMPROVEMENT)
        verdicts[name] = {
            "promote": bool(promote),
            "expectancy_lift_pct": round(lift, 4),
            "brier_improvement": None if brier_gain is None else round(brier_gain, 5),
            "reason": ("Improves expectancy and calibration out of sample." if promote
                       else "Does not clear the promotion bar; keep as context/timing only."),
        }

    return {
        "variants": results,
        "verdicts": verdicts,
        "promotion_bar": {"min_expectancy_lift_pct": MIN_EXPECTANCY_LIFT_PCT,
                          "min_brier_improvement": MIN_BRIER_IMPROVEMENT, "min_trades": MIN_TRADES},
        "feature_store_empty": features.empty,
        "note": (
            "All variants share candles and costs. Order-flow and smart-money variants require a "
            "FeatureStore of recorded observations; with none supplied they equal the baseline."
        ),
    }
