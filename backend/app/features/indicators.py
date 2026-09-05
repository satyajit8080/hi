"""Technical indicators.

Every function returns a full-length array aligned to the input so a value at
index *i* uses only bars 0..i. Nothing here may peek forward — that rule is what
makes the backtest and the live engine agree.
"""
from __future__ import annotations

import numpy as np

Array = np.ndarray


def _as_array(values) -> Array:
    return np.asarray(values, dtype=float)


def sma(values, period: int) -> Array:
    v = _as_array(values)
    out = np.full(v.shape, np.nan)
    if len(v) < period:
        return out
    cumsum = np.cumsum(np.insert(v, 0, 0.0))
    out[period - 1 :] = (cumsum[period:] - cumsum[:-period]) / period
    return out


def ema(values, period: int) -> Array:
    v = _as_array(values)
    out = np.full(v.shape, np.nan)
    if len(v) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = v[:period].mean()
    for i in range(period, len(v)):
        out[i] = alpha * v[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(values, period: int = 14) -> Array:
    """Wilder's RSI."""
    v = _as_array(values)
    out = np.full(v.shape, np.nan)
    if len(v) <= period:
        return out
    delta = np.diff(v)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = gain[:period].mean()
    avg_loss = loss[:period].mean()
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(v)):
        avg_gain = (avg_gain * (period - 1) + gain[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(values, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Array, Array, Array]:
    v = _as_array(values)
    macd_line = ema(v, fast) - ema(v, slow)
    finite = ~np.isnan(macd_line)
    signal_line = np.full(v.shape, np.nan)
    if finite.sum() >= signal:
        signal_line[finite] = ema(macd_line[finite], signal)
    return macd_line, signal_line, macd_line - signal_line


def true_range(high, low, close) -> Array:
    h, l, c = _as_array(high), _as_array(low), _as_array(close)
    prev_close = np.concatenate(([c[0]], c[:-1]))
    return np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))


def atr(high, low, close, period: int = 14) -> Array:
    tr = true_range(high, low, close)
    out = np.full(tr.shape, np.nan)
    if len(tr) < period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def adx(high, low, close, period: int = 14) -> tuple[Array, Array, Array]:
    """Returns (adx, +DI, -DI). ADX >= 25 is the conventional trend threshold."""
    h, l = _as_array(high), _as_array(low)
    n = len(h)
    out_adx = np.full(n, np.nan)
    plus_di = np.full(n, np.nan)
    minus_di = np.full(n, np.nan)
    if n < period * 2:
        return out_adx, plus_di, minus_di

    up = np.diff(h)
    down = -np.diff(l)
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(high, low, close)[1:]

    atr_s = tr[:period].sum()
    plus_s = plus_dm[:period].sum()
    minus_s = minus_dm[:period].sum()
    dx_values: list[float] = []

    for i in range(period, len(tr)):
        atr_s = atr_s - atr_s / period + tr[i]
        plus_s = plus_s - plus_s / period + plus_dm[i]
        minus_s = minus_s - minus_s / period + minus_dm[i]
        if atr_s == 0:
            continue
        pdi = 100 * plus_s / atr_s
        mdi = 100 * minus_s / atr_s
        plus_di[i + 1] = pdi
        minus_di[i + 1] = mdi
        denom = pdi + mdi
        dx = 0.0 if denom == 0 else 100 * abs(pdi - mdi) / denom
        dx_values.append(dx)
        if len(dx_values) == period:
            out_adx[i + 1] = float(np.mean(dx_values))
        elif len(dx_values) > period:
            out_adx[i + 1] = (out_adx[i] * (period - 1) + dx) / period
    return out_adx, plus_di, minus_di


def bollinger(values, period: int = 20, mult: float = 2.0) -> tuple[Array, Array, Array]:
    v = _as_array(values)
    mid = sma(v, period)
    std = np.full(v.shape, np.nan)
    for i in range(period - 1, len(v)):
        std[i] = v[i - period + 1 : i + 1].std(ddof=0)
    return mid + mult * std, mid, mid - mult * std


def bollinger_width(values, period: int = 20, mult: float = 2.0) -> Array:
    upper, mid, lower = bollinger(values, period, mult)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(mid > 0, (upper - lower) / mid, np.nan)


def realized_volatility(close, period: int = 20, bars_per_year: int = 35040) -> Array:
    """Annualised close-to-close realised vol. 35040 = 15m bars in a year."""
    c = _as_array(close)
    out = np.full(c.shape, np.nan)
    if len(c) <= period:
        return out
    log_ret = np.diff(np.log(np.maximum(c, 1e-12)))
    for i in range(period, len(c)):
        out[i] = log_ret[i - period : i].std(ddof=1) * np.sqrt(bars_per_year)
    return out


def obv(close, volume) -> Array:
    c, v = _as_array(close), _as_array(volume)
    out = np.zeros(c.shape)
    for i in range(1, len(c)):
        out[i] = out[i - 1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0.0)
    return out


def vwap(high, low, close, volume) -> Array:
    typical = (_as_array(high) + _as_array(low) + _as_array(close)) / 3.0
    v = _as_array(volume)
    cum_v = np.cumsum(v)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(cum_v > 0, np.cumsum(typical * v) / cum_v, np.nan)


def rate_of_change(values, period: int = 10) -> Array:
    v = _as_array(values)
    out = np.full(v.shape, np.nan)
    if len(v) <= period:
        return out
    prior = v[:-period]
    out[period:] = np.where(prior != 0, (v[period:] - prior) / prior * 100.0, np.nan)
    return out


def percentile_rank(values, lookback: int = 200) -> Array:
    """Rolling percentile of the latest value within its own history (0..1).

    Preferred over a plain z-score for heavy-tailed microstructure series.
    """
    v = _as_array(values)
    out = np.full(v.shape, np.nan)
    for i in range(len(v)):
        start = max(0, i - lookback + 1)
        window = v[start : i + 1]
        window = window[~np.isnan(window)]
        if len(window) >= 20:
            out[i] = float((window <= v[i]).sum()) / len(window)
    return out


def robust_z(values, lookback: int = 200) -> Array:
    """Median/MAD z-score — resistant to the outliers that break a plain z."""
    v = _as_array(values)
    out = np.full(v.shape, np.nan)
    for i in range(len(v)):
        start = max(0, i - lookback + 1)
        window = v[start : i + 1]
        window = window[~np.isnan(window)]
        if len(window) < 20:
            continue
        med = np.median(window)
        mad = np.median(np.abs(window - med))
        scale = 1.4826 * mad
        out[i] = 0.0 if scale < 1e-12 else float((v[i] - med) / scale)
    return out
