"""Market structure: swing points, S/R levels, breaks of structure.

Structure is what makes a stop meaningful. An ATR stop says "how far is far";
a structural stop says "at what price was I wrong". The risk engine uses both
and takes the more conservative.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class SwingPoint:
    index: int
    price: float
    kind: str  # "high" | "low"


@dataclass(slots=True)
class StructureState:
    swings: list[SwingPoint]
    trend: str  # "up" | "down" | "range"
    last_high: float | None
    last_low: float | None
    support: list[float]
    resistance: list[float]
    broke_structure: bool
    break_direction: str | None


def find_swings(high, low, left: int = 2, right: int = 2) -> list[SwingPoint]:
    """Fractal swing points. A swing is only confirmed `right` bars later, so
    the most recent bars intentionally have no swing — that is not a bug, it is
    the absence of look-ahead."""
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    out: list[SwingPoint] = []
    for i in range(left, len(h) - right):
        window_h = h[i - left : i + right + 1]
        window_l = l[i - left : i + right + 1]
        if h[i] == window_h.max() and (window_h == h[i]).sum() == 1:
            out.append(SwingPoint(i, float(h[i]), "high"))
        if l[i] == window_l.min() and (window_l == l[i]).sum() == 1:
            out.append(SwingPoint(i, float(l[i]), "low"))
    out.sort(key=lambda s: s.index)
    return out


def cluster_levels(prices: list[float], tolerance: float = 0.004) -> list[float]:
    """Group nearby swing prices into levels; more touches sort first."""
    if not prices:
        return []
    ordered = sorted(prices)
    clusters: list[list[float]] = [[ordered[0]]]
    for price in ordered[1:]:
        if abs(price - clusters[-1][-1]) / max(clusters[-1][-1], 1e-9) <= tolerance:
            clusters[-1].append(price)
        else:
            clusters.append([price])
    scored = sorted(clusters, key=len, reverse=True)
    return [float(np.mean(c)) for c in scored]


def analyse_structure(high, low, close, left: int = 2, right: int = 2) -> StructureState:
    swings = find_swings(high, low, left, right)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    trend = "range"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price < lows[-2].price
        if hh and hl:
            trend = "up"
        elif lh and ll:
            trend = "down"

    last_price = float(np.asarray(close, dtype=float)[-1])
    resistance = [p for p in cluster_levels([s.price for s in highs]) if p > last_price][:4]
    support = [p for p in cluster_levels([s.price for s in lows]) if p < last_price][:4]

    broke = False
    break_dir: str | None = None
    if highs and last_price > highs[-1].price:
        broke, break_dir = True, "up"
    elif lows and last_price < lows[-1].price:
        broke, break_dir = True, "down"

    return StructureState(
        swings=swings,
        trend=trend,
        last_high=highs[-1].price if highs else None,
        last_low=lows[-1].price if lows else None,
        support=support,
        resistance=resistance,
        broke_structure=broke,
        break_direction=break_dir,
    )


def nearest_level(price: float, levels: list[float]) -> tuple[float | None, float]:
    """Closest level and its distance as a fraction of price."""
    if not levels:
        return None, float("inf")
    level = min(levels, key=lambda x: abs(x - price))
    return level, abs(level - price) / max(price, 1e-9)


def structure_stop(direction: str, state: StructureState, price: float, buffer: float = 0.0015):
    """Stop placed just beyond the last opposing swing, or None if unavailable."""
    if direction == "LONG" and state.last_low is not None and state.last_low < price:
        return state.last_low * (1 - buffer)
    if direction == "SHORT" and state.last_high is not None and state.last_high > price:
        return state.last_high * (1 + buffer)
    return None
