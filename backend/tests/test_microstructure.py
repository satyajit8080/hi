"""Order-flow maths, including the sign conventions that are easy to get wrong."""
from __future__ import annotations

import pytest

from app.features.microstructure import (
    BookPersistenceTracker,
    BookSnapshot,
    CVDAccumulator,
    Trade,
    cvd_divergence,
    micro_price,
    order_book_imbalance,
    order_flow_imbalance,
    queue_imbalance,
    trade_flow_imbalance,
    weighted_order_book_imbalance,
)


def book(bid=100.0, ask=100.1, bid_qty=10.0, ask_qty=10.0, ts=1000) -> BookSnapshot:
    return BookSnapshot(
        ts_ms=ts,
        bids=[(bid - i * 0.1, bid_qty) for i in range(5)],
        asks=[(ask + i * 0.1, ask_qty) for i in range(5)],
    )


def test_balanced_book_has_zero_imbalance():
    assert order_book_imbalance(book()) == pytest.approx(0.0)


def test_imbalance_is_bounded_and_signed():
    assert order_book_imbalance(book(bid_qty=30, ask_qty=10)) == pytest.approx(0.5)
    assert order_book_imbalance(book(bid_qty=10, ask_qty=30)) == pytest.approx(-0.5)
    assert order_book_imbalance(book(bid_qty=100, ask_qty=0)) == pytest.approx(1.0)


def test_micro_price_leans_away_from_the_heavier_side():
    # Heavy bid, light ask: price is more likely to tick up, so micro-price
    # should sit above the mid.
    b = book(bid_qty=50, ask_qty=5)
    assert micro_price(b) > b.mid
    assert micro_price(book(bid_qty=5, ask_qty=50)) < b.mid


def test_micro_price_equals_mid_when_balanced():
    b = book()
    assert micro_price(b) == pytest.approx(b.mid)


def test_ofi_positive_when_bid_improves():
    prev = book(bid=100.0, ask=100.1)
    curr = book(bid=100.05, ask=100.1)
    assert order_flow_imbalance(prev, curr) > 0


def test_ofi_negative_when_ask_improves():
    prev = book(bid=100.0, ask=100.1)
    curr = book(bid=100.0, ask=100.05)
    assert order_flow_imbalance(prev, curr) < 0


def test_ofi_reflects_size_added_at_the_same_price():
    prev = book(bid_qty=10)
    curr = book(bid_qty=25)
    assert order_flow_imbalance(prev, curr) == pytest.approx(15.0)


def test_cvd_sign_convention_buyer_maker_means_seller_aggressed():
    cvd = CVDAccumulator()
    cvd.add(Trade(1, 100.0, 5.0, is_buyer_maker=True))    # aggressive sell
    assert cvd.value == pytest.approx(-5.0)
    cvd.add(Trade(2, 100.0, 8.0, is_buyer_maker=False))   # aggressive buy
    assert cvd.value == pytest.approx(3.0)


def test_cvd_session_reset():
    cvd = CVDAccumulator(reset_interval_ms=1000)
    cvd.add(Trade(0, 100.0, 5.0, False))
    cvd.add(Trade(2000, 100.0, 1.0, False))
    assert cvd.value == pytest.approx(1.0)


def test_trade_flow_imbalance_bounds():
    buys = [Trade(1, 100, 1, False) for _ in range(3)]
    sells = [Trade(1, 100, 1, True) for _ in range(1)]
    assert trade_flow_imbalance(buys + sells) == pytest.approx(0.5)
    assert trade_flow_imbalance([]) == 0.0


def test_cvd_divergence_detects_price_up_flow_down():
    prices = list(range(100, 130))
    cvd = list(range(30, 0, -1))
    assert cvd_divergence(prices, cvd) < -0.5


def test_persistence_discounts_freshly_placed_size():
    tracker = BookPersistenceTracker(half_life_ms=3000)
    fresh = book(bid_qty=100, ask_qty=10, ts=0)
    tracker.update(fresh)
    # Everything is brand new, so confidence is ~0 and imbalance is muted.
    assert abs(tracker.persistent_imbalance(fresh)) < 0.2

    aged = BookSnapshot(ts_ms=30_000, bids=fresh.bids, asks=fresh.asks)
    assert tracker.persistent_imbalance(aged) > 0.5


def test_queue_imbalance_matches_top_of_book_only():
    assert queue_imbalance(book(bid_qty=30, ask_qty=10)) == pytest.approx(0.5)


def test_weighted_imbalance_discounts_far_levels():
    near = BookSnapshot(1, bids=[(100.0, 10.0)], asks=[(100.1, 10.0)])
    far = BookSnapshot(1, bids=[(100.0, 10.0), (90.0, 500.0)], asks=[(100.1, 10.0)])
    # A wall 10% away should barely move the weighted imbalance.
    assert abs(weighted_order_book_imbalance(far) - weighted_order_book_imbalance(near)) < 0.05


def test_crossed_book_is_detected():
    assert BookSnapshot(1, bids=[(101.0, 1)], asks=[(100.0, 1)]).is_crossed() is True
