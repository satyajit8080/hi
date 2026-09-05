"""Exchange adapter interface.

Every venue reduces to the same four event types so the feature engine never
learns an exchange's vocabulary:

    MarketEvent(kind="trade" | "book_diff" | "book_snapshot" | "liquidation")

Public market data needs no API key on any supported venue. Keys in `.env` are
only for private endpoints and lifted rate limits, and the platform runs fully
without them.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

EventKind = Literal["trade", "book_diff", "book_snapshot", "liquidation", "candle", "heartbeat"]


@dataclass(slots=True)
class MarketEvent:
    kind: EventKind
    venue: str
    symbol: str
    ts_ms: int
    payload: dict[str, Any] = field(default_factory=dict)
    is_simulated: bool = False


class ExchangeAdapter(abc.ABC):
    """One instance per venue."""

    name: str = "base"
    supports_liquidations: bool = False

    def __init__(self, symbols: list[str], depth: int = 20) -> None:
        self.symbols = symbols
        self.depth = depth

    @abc.abstractmethod
    async def stream(self) -> AsyncIterator[MarketEvent]:
        """Yield normalised market events until cancelled."""
        raise NotImplementedError

    @abc.abstractmethod
    async def fetch_book_snapshot(self, symbol: str) -> MarketEvent:
        """REST depth snapshot used to (re)seed a local book."""
        raise NotImplementedError

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
        return []

    async def close(self) -> None:
        return None

    # Venue-native symbol formatting (BTCUSDT -> BTC-USD etc).
    def to_venue_symbol(self, symbol: str) -> str:
        return symbol

    def from_venue_symbol(self, symbol: str) -> str:
        return symbol
