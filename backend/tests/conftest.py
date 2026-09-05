from __future__ import annotations

import numpy as np
import pytest

from app.features.regime import build_mtf_view, classify_regime
from app.features.snapshot import (
    Candles,
    ContextFeatures,
    DataQuality,
    DerivFeatures,
    FeatureSnapshot,
    MicroFeatures,
    compute_ta_block,
)
from app.features.structure import analyse_structure


def make_candles(n=300, trend=0.0015, noise=0.004, start=64000.0, seed=3) -> Candles:
    rng = np.random.default_rng(seed)
    close = [start]
    for _ in range(n - 1):
        close.append(close[-1] * (1 + trend + rng.normal(0, noise)))
    high = [c * (1 + abs(rng.normal(0, noise / 2))) for c in close]
    low = [c * (1 - abs(rng.normal(0, noise / 2))) for c in close]
    opens = [close[0]] + close[:-1]
    volume = list(rng.uniform(800, 2400, n))
    ts = [1_700_000_000_000 + i * 3_600_000 for i in range(n)]
    return Candles(ts=ts, open=opens, high=high, low=low, close=close, volume=volume)


def make_snapshot(
    timeframe="1h", candles=None, micro=None, context=None, degraded=False, venues_live=3
) -> FeatureSnapshot:
    c = candles or make_candles()
    regime = classify_regime(c.high, c.low, c.close)
    regimes = {"1d": regime, "4h": regime, "1h": regime, timeframe: regime}
    return FeatureSnapshot(
        symbol="BTCUSDT",
        timeframe=timeframe,
        taken_at="2026-01-01T00:00:00+00:00",
        candle_ts=c.ts[-1],
        price=c.last_close,
        ta=compute_ta_block(c),
        regime=regime,
        regimes_by_tf=regimes,
        mtf=build_mtf_view(regimes),
        structure=analyse_structure(c.high, c.low, c.close),
        micro=micro or MicroFeatures(venue="binance", ts_ms=c.ts[-1], available=False),
        deriv=DerivFeatures(available=False),
        context=context or ContextFeatures(available=False),
        quality=DataQuality(
            venues_live=venues_live, book_synced=True, max_staleness_ms=200,
            clock_skew_ms=0, degraded=degraded, flags=["stale"] if degraded else [],
        ),
        is_simulated=True,
    )


@pytest.fixture
def candles():
    return make_candles()


@pytest.fixture
def snapshot():
    return make_snapshot()
