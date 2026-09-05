"""Indicator correctness and, above all, absence of look-ahead."""
from __future__ import annotations

import numpy as np
import pytest

from app.features import indicators as ind


def series(n=300, start=100.0, drift=0.1):
    return [start + drift * i + 3 * np.sin(i / 7) for i in range(n)]


def test_sma_matches_manual_mean():
    v = [1, 2, 3, 4, 5]
    assert ind.sma(v, 3)[2] == pytest.approx(2.0)
    assert np.isnan(ind.sma(v, 3)[1])


def test_ema_responds_faster_than_sma():
    """Right after a step change the EMA has moved further than the SMA."""
    v = [10.0] * 50 + [20.0] * 3
    assert ind.ema(v, 10)[-1] > ind.sma(v, 10)[-1]


def test_rsi_bounds_and_extremes():
    rising = list(range(1, 60))
    assert ind.rsi(rising, 14)[-1] > 95
    falling = list(range(60, 1, -1))
    assert ind.rsi(falling, 14)[-1] < 5


def test_atr_is_positive_and_tracks_range():
    n = 100
    high = [10 + i * 0.1 + 1 for i in range(n)]
    low = [10 + i * 0.1 - 1 for i in range(n)]
    close = [10 + i * 0.1 for i in range(n)]
    atr = ind.atr(high, low, close, 14)
    assert atr[-1] > 1.5


def test_no_lookahead_prefix_stability():
    """The single most important indicator test: computing on a longer series
    must not change a value that was already available at an earlier bar."""
    full = series(400)
    for fn in (lambda x: ind.rsi(x, 14), lambda x: ind.ema(x, 20), lambda x: ind.rate_of_change(x, 10)):
        long_result = fn(full)
        short_result = fn(full[:300])
        assert long_result[250] == pytest.approx(short_result[250], rel=1e-9)


def test_atr_no_lookahead():
    n = 400
    high = [100 + i * 0.05 + 1 for i in range(n)]
    low = [100 + i * 0.05 - 1 for i in range(n)]
    close = [100 + i * 0.05 for i in range(n)]
    full = ind.atr(high, low, close, 14)
    partial = ind.atr(high[:300], low[:300], close[:300], 14)
    assert full[250] == pytest.approx(partial[250], rel=1e-9)


def test_robust_z_is_outlier_resistant():
    rng = np.random.default_rng(0)
    values = list(rng.normal(0.0, 1.0, 200)) + [50.0]
    z = ind.robust_z(values)
    assert z[-1] > 10                 # the outlier is flagged loudly
    assert abs(z[150]) < 5            # ...without distorting the body

    # A plain z-score would be dragged toward the outlier; the median/MAD
    # version leaves the bulk of the distribution alone.
    contaminated = ind.robust_z(values)[150]
    clean = ind.robust_z(values[:200])[150]
    assert abs(contaminated - clean) < 1e-9


def test_percentile_rank_ranges_zero_to_one():
    p = ind.percentile_rank(list(range(200)))
    assert p[-1] == pytest.approx(1.0)
    assert 0.0 <= p[100] <= 1.0


def test_adx_returns_finite_values_on_a_trend():
    n = 200
    high = [100 + i * 0.5 + 1 for i in range(n)]
    low = [100 + i * 0.5 - 1 for i in range(n)]
    close = [100 + i * 0.5 for i in range(n)]
    adx, plus_di, minus_di = ind.adx(high, low, close, 14)
    assert not np.isnan(adx[-1])
    assert plus_di[-1] > minus_di[-1]
