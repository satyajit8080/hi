"""Candle utilities with no I/O dependencies."""
from __future__ import annotations

TF_MS = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
         "4h": 14_400_000, "1d": 86_400_000}


def drop_unclosed(rows: list[dict], timeframe: str, now_ms: int) -> list[dict]:
    """Remove the bar still in progress.

    Exchange kline endpoints return the current, unfinished candle as the last
    row. Scoring it means the live engine sees a bar the backtest never will,
    which is the most common way a live system quietly stops matching its own
    backtest. A bar is closed only when its open time plus the timeframe is in
    the past.
    """
    tf_ms = TF_MS.get(timeframe, 3_600_000)
    return [r for r in rows if r["ts"] + tf_ms <= now_ms]
