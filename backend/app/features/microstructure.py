"""Order-flow and microstructure features.

Implements the metrics the research put in tier V1, with the roles it assigned:

  spread          -> risk gate (never a directional vote)
  OBI             -> entry timing / confirmation, 5m-15m only
  OFI             -> entry trigger, 5m only  (Cont, Kukanov & Stoikov 2014)
  micro-price     -> fair-value reference    (Stoikov 2017)
  CVD             -> confirmation, divergence acts as a veto
  trade imbalance -> confirmation
  large prints    -> context flag

The horizon discipline is enforced structurally in engine/scoring.py: these
values are only ever offered to the entry-timing sub-model. A feature that
decays in seconds does not get a vote on a 4-hour thesis.

Anti-spoofing (research §3): depth is weighted by how long it has rested and by
its distance from the touch, so quotes that flicker in and out contribute far
less than resting size.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

Level = tuple[float, float]  # (price, qty)


# ────────────────────────────── book snapshot ───────────────────────────────


@dataclass(slots=True)
class BookSnapshot:
    ts_ms: int
    bids: list[Level]  # descending by price
    asks: list[Level]  # ascending by price
    synced: bool = True

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else float("nan")

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else float("nan")

    @property
    def best_bid_qty(self) -> float:
        return self.bids[0][1] if self.bids else 0.0

    @property
    def best_ask_qty(self) -> float:
        return self.asks[0][1] if self.asks else 0.0

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_abs(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_rel(self) -> float:
        m = self.mid
        return self.spread_abs / m if m > 0 else float("nan")

    def is_crossed(self) -> bool:
        return bool(self.bids and self.asks and self.best_bid >= self.best_ask)


def order_book_imbalance(book: BookSnapshot, levels: int = 5) -> float:
    """(bid size - ask size) / total size over the top N levels. Range [-1, 1]."""
    bid = sum(q for _, q in book.bids[:levels])
    ask = sum(q for _, q in book.asks[:levels])
    total = bid + ask
    return 0.0 if total <= 0 else (bid - ask) / total


def weighted_order_book_imbalance(book: BookSnapshot, levels: int = 20, decay: float = 0.15) -> float:
    """OBI with size discounted by distance from the mid.

    Displayed depth far from the touch is the cheapest thing to fake, so it is
    weighted down exponentially. `decay` is in units of relative distance.
    """
    mid = book.mid
    if not np.isfinite(mid) or mid <= 0:
        return 0.0

    def side_weight(levels_in: Iterable[Level]) -> float:
        total = 0.0
        for price, qty in levels_in:
            dist = abs(price - mid) / mid
            total += qty * float(np.exp(-dist / max(decay, 1e-9) * 100))
        return total

    bid = side_weight(book.bids[:levels])
    ask = side_weight(book.asks[:levels])
    total = bid + ask
    return 0.0 if total <= 0 else (bid - ask) / total


def micro_price(book: BookSnapshot) -> float:
    """Size-weighted price between the touches (Stoikov 2017).

    Leans toward the side with less size, i.e. toward where the book is likely
    to move next, which makes it a better short-horizon reference than the mid.
    """
    bq, aq = book.best_bid_qty, book.best_ask_qty
    total = bq + aq
    if total <= 0:
        return book.mid
    return (book.best_bid * aq + book.best_ask * bq) / total


def queue_imbalance(book: BookSnapshot) -> float:
    """Top-of-book size ratio. Range [-1, 1]."""
    bq, aq = book.best_bid_qty, book.best_ask_qty
    total = bq + aq
    return 0.0 if total <= 0 else (bq - aq) / total


def order_flow_imbalance(prev: BookSnapshot, curr: BookSnapshot) -> float:
    """Best-level OFI, Cont-Kukanov-Stoikov (2014).

    A bid that improves or adds size is demand; a bid that retreats or shrinks
    removes it. The ask side mirrors that with the opposite sign. The sum over
    an interval is close to linear in the price change over the same interval,
    with a slope inversely proportional to depth.
    """
    e = 0.0
    if curr.best_bid > prev.best_bid:
        e += curr.best_bid_qty
    elif curr.best_bid == prev.best_bid:
        e += curr.best_bid_qty - prev.best_bid_qty
    else:
        e -= prev.best_bid_qty

    if curr.best_ask < prev.best_ask:
        e -= curr.best_ask_qty
    elif curr.best_ask == prev.best_ask:
        e -= curr.best_ask_qty - prev.best_ask_qty
    else:
        e += prev.best_ask_qty
    return float(e)


# ─────────────────────────── persistence tracking ───────────────────────────


@dataclass
class BookPersistenceTracker:
    """Tracks how long price levels have rested, to discount flickering quotes.

    Layering and spoofing both look like large depth that never trades. Neither
    survives a persistence weight, because the size is pulled long before it
    would have to be honoured.
    """

    half_life_ms: float = 3000.0
    _first_seen: dict[tuple[str, float], int] = field(default_factory=dict)

    def update(self, book: BookSnapshot) -> None:
        live: set[tuple[str, float]] = set()
        for price, _ in book.bids:
            key = ("b", round(price, 8))
            live.add(key)
            self._first_seen.setdefault(key, book.ts_ms)
        for price, _ in book.asks:
            key = ("a", round(price, 8))
            live.add(key)
            self._first_seen.setdefault(key, book.ts_ms)
        for key in list(self._first_seen):
            if key not in live:
                del self._first_seen[key]

    def persistent_imbalance(self, book: BookSnapshot, levels: int = 20) -> float:
        """OBI where each level's size is scaled by an age-based confidence."""

        def weight(side: str, price: float, qty: float) -> float:
            seen = self._first_seen.get((side, round(price, 8)), book.ts_ms)
            age_ms = max(0.0, book.ts_ms - seen)
            confidence = 1.0 - float(np.exp(-age_ms / max(self.half_life_ms, 1.0)))
            return qty * confidence

        bid = sum(weight("b", p, q) for p, q in book.bids[:levels])
        ask = sum(weight("a", p, q) for p, q in book.asks[:levels])
        total = bid + ask
        return 0.0 if total <= 0 else (bid - ask) / total


# ─────────────────────────────── trade flow ─────────────────────────────────


@dataclass(slots=True)
class Trade:
    ts_ms: int
    price: float
    qty: float
    is_buyer_maker: bool  # Binance aggTrade "m": True => the aggressor sold

    @property
    def signed_qty(self) -> float:
        return -self.qty if self.is_buyer_maker else self.qty

    @property
    def notional(self) -> float:
        return self.price * self.qty


class CVDAccumulator:
    """Cumulative Volume Delta.

    Signed by aggressor: `is_buyer_maker == True` means a resting bid was hit,
    so the aggressor was a seller and the delta is negative. Spot and perp CVD
    are tracked separately — they routinely diverge for hours and the divergence
    is itself the information.
    """

    def __init__(self, reset_interval_ms: int | None = None) -> None:
        self.reset_interval_ms = reset_interval_ms
        self.value = 0.0
        self.session_start_ms: int | None = None

    def add(self, trade: Trade) -> float:
        if self.session_start_ms is None:
            self.session_start_ms = trade.ts_ms
        if (
            self.reset_interval_ms
            and trade.ts_ms - self.session_start_ms >= self.reset_interval_ms
        ):
            self.value = 0.0
            self.session_start_ms = trade.ts_ms
        self.value += trade.signed_qty
        return self.value


def trade_flow_imbalance(trades: list[Trade]) -> float:
    """Net aggressive volume over total, in [-1, 1]."""
    buy = sum(t.qty for t in trades if not t.is_buyer_maker)
    sell = sum(t.qty for t in trades if t.is_buyer_maker)
    total = buy + sell
    return 0.0 if total <= 0 else (buy - sell) / total


def large_print_score(trades: list[Trade], lookback_notional: list[float]) -> float:
    """How unusual the largest print in this window is, as a robust z-score."""
    if not trades or len(lookback_notional) < 20:
        return 0.0
    largest = max(t.notional for t in trades)
    hist = np.asarray(lookback_notional, dtype=float)
    med = float(np.median(hist))
    mad = float(np.median(np.abs(hist - med)))
    scale = 1.4826 * mad
    return 0.0 if scale < 1e-9 else float((largest - med) / scale)


def cvd_divergence(prices: list[float], cvd: list[float], window: int = 20) -> float:
    """Signed divergence between price direction and CVD direction.

    Positive means CVD is stronger than price (buyers absorbing), negative means
    price is rising without buy-side flow behind it. Used as a veto, not a
    trigger — a divergence can persist far longer than a position can.
    """
    if len(prices) < window or len(cvd) < window:
        return 0.0
    p = np.asarray(prices[-window:], dtype=float)
    c = np.asarray(cvd[-window:], dtype=float)
    p_range = p.max() - p.min()
    c_range = c.max() - c.min()
    if p_range <= 0 or c_range <= 0:
        return 0.0
    p_norm = (p[-1] - p[0]) / p_range
    c_norm = (c[-1] - c[0]) / c_range
    return float(c_norm - p_norm)


def effective_spread(trade_price: float, mid_at_trade: float, is_buy: bool) -> float:
    """2 * |price - mid| signed by direction — what the taker actually paid."""
    if mid_at_trade <= 0:
        return float("nan")
    direction = 1.0 if is_buy else -1.0
    return 2.0 * direction * (trade_price - mid_at_trade) / mid_at_trade


def kyle_lambda(signed_volume: list[float], price_changes: list[float]) -> float:
    """Price impact per unit signed volume (OLS slope). Higher => thinner book."""
    if len(signed_volume) < 20 or len(signed_volume) != len(price_changes):
        return float("nan")
    x = np.asarray(signed_volume, dtype=float)
    y = np.asarray(price_changes, dtype=float)
    denom = float((x**2).sum())
    return float("nan") if denom <= 0 else float((x * y).sum() / denom)
