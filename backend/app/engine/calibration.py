"""Confidence calibration.

A strength of 84/100 is a *score*, not a probability. This module maps scores to
an empirical win rate, and refuses to show one until the bucket has enough
closed signals to mean anything.

Default method is Platt scaling: on the small, imbalanced samples a young
product actually has, a two-parameter sigmoid is stabler than isotonic
regression, which will happily fit noise. Isotonic is implemented and takes over
once a bucket is large enough — the switch is by measured Brier score, not
preference.

Reported alongside every rate:
  * the sample size it came from
  * a Wilson 95% interval
  * an explicit "insufficient sample" flag below the floor
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.config import settings


@dataclass(slots=True)
class CalibrationResult:
    win_rate: float | None
    sample_size: int
    ci_low: float | None
    ci_high: float | None
    method: str
    sufficient: bool
    note: str


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — behaves at small n where normal approx does not."""
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * n)) / n) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


class PlattCalibrator:
    """Logistic mapping from raw score to probability, fitted by Newton steps."""

    def __init__(self, a: float = 0.0, b: float = 0.0) -> None:
        self.a = a
        self.b = b

    def fit(self, scores: np.ndarray, labels: np.ndarray, iters: int = 100) -> "PlattCalibrator":
        x = np.asarray(scores, dtype=float)
        y = np.asarray(labels, dtype=float)
        if len(x) < 10:
            raise ValueError("Platt scaling needs at least 10 observations")
        # Platt's prior correction keeps the fit from saturating on small n.
        n_pos, n_neg = float(y.sum()), float(len(y) - y.sum())
        hi = (n_pos + 1) / (n_pos + 2) if n_pos else 0.5
        lo = 1 / (n_neg + 2) if n_neg else 0.5
        t = np.where(y > 0.5, hi, lo)

        a, b = 0.0, 0.0
        for _ in range(iters):
            z = a * x + b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))
            grad_a = float(((p - t) * x).sum())
            grad_b = float((p - t).sum())
            w = p * (1 - p) + 1e-9
            h_aa = float((w * x * x).sum()) + 1e-9
            h_ab = float((w * x).sum())
            h_bb = float(w.sum()) + 1e-9
            det = h_aa * h_bb - h_ab * h_ab
            if abs(det) < 1e-12:
                break
            da = (h_bb * grad_a - h_ab * grad_b) / det
            db = (h_aa * grad_b - h_ab * grad_a) / det
            a -= da
            b -= db
            if max(abs(da), abs(db)) < 1e-9:
                break
        self.a, self.b = a, b
        return self

    def predict(self, score: float) -> float:
        z = self.a * float(score) + self.b
        return float(1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z)))))

    def params(self) -> dict[str, float]:
        return {"a": self.a, "b": self.b}

    @classmethod
    def from_params(cls, params: dict) -> "PlattCalibrator":
        return cls(float(params.get("a", 0.0)), float(params.get("b", 0.0)))


class IsotonicCalibrator:
    """Pool-adjacent-violators isotonic fit. Flexible, needs a bigger sample."""

    def __init__(self, x: list[float] | None = None, y: list[float] | None = None) -> None:
        self.x = x or []
        self.y = y or []

    def fit(self, scores: np.ndarray, labels: np.ndarray) -> "IsotonicCalibrator":
        order = np.argsort(scores)
        xs = np.asarray(scores, dtype=float)[order]
        ys = np.asarray(labels, dtype=float)[order]
        values = list(ys)
        weights = [1.0] * len(ys)
        i = 0
        while i < len(values) - 1:
            if values[i] <= values[i + 1]:
                i += 1
                continue
            total_w = weights[i] + weights[i + 1]
            merged = (values[i] * weights[i] + values[i + 1] * weights[i + 1]) / total_w
            values[i : i + 2] = [merged]
            weights[i : i + 2] = [total_w]
            xs = np.delete(xs, i + 1)
            i = max(i - 1, 0)
        self.x = [float(v) for v in xs]
        self.y = [float(v) for v in values]
        return self

    def predict(self, score: float) -> float:
        if not self.x:
            return 0.5
        return float(np.interp(float(score), self.x, self.y))

    def params(self) -> dict:
        return {"x": self.x, "y": self.y}

    @classmethod
    def from_params(cls, params: dict) -> "IsotonicCalibrator":
        return cls(list(params.get("x", [])), list(params.get("y", [])))


# ───────────────────────────── quality metrics ──────────────────────────────


def brier_score(probs: np.ndarray, labels: np.ndarray) -> float:
    return float(np.mean((np.asarray(probs, float) - np.asarray(labels, float)) ** 2))


def log_loss(probs: np.ndarray, labels: np.ndarray, eps: float = 1e-12) -> float:
    p = np.clip(np.asarray(probs, float), eps, 1 - eps)
    y = np.asarray(labels, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def expected_calibration_error(probs: np.ndarray, labels: np.ndarray, bins: int = 10) -> float:
    p = np.asarray(probs, float)
    y = np.asarray(labels, float)
    if len(p) == 0:
        return float("nan")
    edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    for i in range(bins):
        mask = (p > edges[i]) & (p <= edges[i + 1]) if i else (p >= edges[i]) & (p <= edges[i + 1])
        if mask.sum() == 0:
            continue
        ece += mask.sum() / len(p) * abs(y[mask].mean() - p[mask].mean())
    return float(ece)


def reliability_curve(probs: np.ndarray, labels: np.ndarray, bins: int = 10) -> list[dict]:
    """Points for the reliability diagram shown on the public performance page."""
    p = np.asarray(probs, float)
    y = np.asarray(labels, float)
    edges = np.linspace(0, 1, bins + 1)
    out: list[dict] = []
    for i in range(bins):
        mask = (p > edges[i]) & (p <= edges[i + 1]) if i else (p >= edges[i]) & (p <= edges[i + 1])
        n = int(mask.sum())
        if n == 0:
            out.append({"bin_low": float(edges[i]), "bin_high": float(edges[i + 1]),
                        "predicted": None, "observed": None, "n": 0,
                        "ci_low": None, "ci_high": None})
            continue
        wins = int(y[mask].sum())
        lo, hi = wilson_interval(wins, n)
        out.append({
            "bin_low": float(edges[i]),
            "bin_high": float(edges[i + 1]),
            "predicted": float(p[mask].mean()),
            "observed": float(y[mask].mean()),
            "n": n,
            "ci_low": lo,
            "ci_high": hi,
        })
    return out


def empirical_bucket(
    strength: int,
    outcomes: list[tuple[int, bool]],
    band: int = 10,
) -> CalibrationResult:
    """Win rate for historical signals whose strength sits in the same band.

    Below the minimum sample we return no number at all rather than a
    reassuring-looking one. A 5-trade bucket showing "80% win rate" is the exact
    dishonesty this product exists to avoid.
    """
    low, high = strength - band, strength + band
    matched = [(s, w) for s, w in outcomes if low <= s <= high]
    n = len(matched)
    wins = sum(1 for _, w in matched if w)
    floor = settings.min_calibration_sample

    if n < floor:
        return CalibrationResult(
            win_rate=None,
            sample_size=n,
            ci_low=None,
            ci_high=None,
            method="empirical",
            sufficient=False,
            note=(
                f"Only {n} closed signals in this strength band. We need at least {floor} "
                "before publishing a win rate."
            ),
        )

    lo, hi = wilson_interval(wins, n)
    return CalibrationResult(
        win_rate=wins / n,
        sample_size=n,
        ci_low=lo,
        ci_high=hi,
        method="empirical",
        sufficient=True,
        note=f"Based on {n} closed signals with strength {low}-{high}.",
    )


def choose_method(
    scores: np.ndarray, labels: np.ndarray
) -> tuple[str, PlattCalibrator | IsotonicCalibrator, dict[str, float]]:
    """Fit both, keep whichever has the lower Brier score on the same data.

    Isotonic normally wins on large samples; Platt normally wins on small ones.
    We let the measurement decide rather than asserting either.
    """
    platt = PlattCalibrator().fit(scores, labels)
    p_probs = np.array([platt.predict(s) for s in scores])
    best_name, best_model, best_probs = "platt", platt, p_probs

    if len(scores) >= 200:
        iso = IsotonicCalibrator().fit(scores, labels)
        i_probs = np.array([iso.predict(s) for s in scores])
        if brier_score(i_probs, labels) < brier_score(p_probs, labels):
            best_name, best_model, best_probs = "isotonic", iso, i_probs

    metrics = {
        "brier": brier_score(best_probs, labels),
        "log_loss": log_loss(best_probs, labels),
        "ece": expected_calibration_error(best_probs, labels),
    }
    return best_name, best_model, metrics
