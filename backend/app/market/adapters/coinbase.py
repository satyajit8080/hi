"""Coinbase adapter — the corroborating venue.

Two venues is the minimum the engine will publish on. A single book is the
easiest thing in crypto to spoof, so an imbalance that only one exchange can see
gets its weight halved and, below `SP_MIN_LIVE_VENUES`, no signal is published
at all.

Uses the public Advanced Trade market data socket: `level2` for depth and
`market_trades` for the tape. No key needed for public channels.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import AsyncIterator

import httpx
import websockets

from app.market.adapters.base import ExchangeAdapter, MarketEvent

WS_URL = "wss://advanced-trade-ws.coinbase.com"
REST_URL = "https://api.exchange.coinbase.com"

GRANULARITY = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 21600, "1d": 86400}


class CoinbaseAdapter(ExchangeAdapter):
    name = "coinbase"

    def __init__(self, symbols: list[str], depth: int = 20) -> None:
        super().__init__(symbols, depth)
        self._client = httpx.AsyncClient(base_url=REST_URL, timeout=10.0)
        self._backoff = 1.0
        self._seq = 0

    def to_venue_symbol(self, symbol: str) -> str:
        # BTCUSDT -> BTC-USD. Coinbase quotes USD, not USDT, for these majors;
        # for corroboration purposes the basis difference is immaterial.
        base = symbol.replace("USDT", "").replace("USD", "")
        return f"{base}-USD"

    def from_venue_symbol(self, symbol: str) -> str:
        return symbol.replace("-USD", "") + "USDT"

    async def stream(self) -> AsyncIterator[MarketEvent]:
        products = [self.to_venue_symbol(s) for s in self.symbols]
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20, max_queue=4096) as ws:
                    for channel in ("level2", "market_trades"):
                        await ws.send(json.dumps({
                            "type": "subscribe", "product_ids": products, "channel": channel
                        }))
                    self._backoff = 1.0
                    yield MarketEvent("heartbeat", self.name, "*", _now_ms(), {"state": "connected"})
                    async for raw in ws:
                        for event in self._normalise(json.loads(raw)):
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                yield MarketEvent("heartbeat", self.name, "*", _now_ms(),
                                  {"state": "disconnected", "error": str(exc)[:200]})
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)

    def _normalise(self, msg: dict) -> list[MarketEvent]:
        out: list[MarketEvent] = []
        channel = msg.get("channel")
        for event in msg.get("events", []):
            if channel == "l2_data":
                symbol = self.from_venue_symbol(event.get("product_id", ""))
                bids: list[tuple[float, float]] = []
                asks: list[tuple[float, float]] = []
                for u in event.get("updates", []):
                    level = (float(u["price_level"]), float(u["new_quantity"]))
                    (bids if u["side"] == "bid" else asks).append(level)
                self._seq += 1
                kind = "book_snapshot" if event.get("type") == "snapshot" else "book_diff"
                payload = {"bids": bids, "asks": asks}
                if kind == "book_snapshot":
                    payload["last_update_id"] = self._seq
                    payload["top_only"] = False
                else:
                    # Coinbase has no per-message sequence ids in this channel, so
                    # we synthesise a monotonic one; gap detection here relies on
                    # the socket's own ordering guarantee plus staleness checks.
                    payload |= {
                        "first_update_id": self._seq,
                        "final_update_id": self._seq,
                        "prev_update_id": None,
                    }
                out.append(MarketEvent(kind, self.name, symbol, _now_ms(), payload))

            elif channel == "market_trades":
                for t in event.get("trades", []):
                    out.append(MarketEvent(
                        "trade", self.name, self.from_venue_symbol(t["product_id"]),
                        _parse_ts(t.get("time")),
                        {
                            "price": float(t["price"]),
                            "qty": float(t["size"]),
                            # Coinbase reports the taker side directly.
                            "is_buyer_maker": t.get("side") == "SELL",
                            "trade_id": t.get("trade_id"),
                        },
                    ))
        return out

    async def fetch_book_snapshot(self, symbol: str) -> MarketEvent:
        product = self.to_venue_symbol(symbol)
        r = await self._client.get(f"/products/{product}/book", params={"level": 2})
        r.raise_for_status()
        d = r.json()
        self._seq += 1
        return MarketEvent(
            "book_snapshot", self.name, symbol, _now_ms(),
            {
                "last_update_id": self._seq,
                "bids": [(float(p), float(q)) for p, q, *_ in d.get("bids", [])[: self.depth * 5]],
                "asks": [(float(p), float(q)) for p, q, *_ in d.get("asks", [])[: self.depth * 5]],
                "top_only": False,
            },
        )

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int = 300) -> list[dict]:
        product = self.to_venue_symbol(symbol)
        r = await self._client.get(
            f"/products/{product}/candles",
            params={"granularity": GRANULARITY.get(timeframe, 3600)},
        )
        if r.status_code != 200:
            return []
        rows = sorted(r.json(), key=lambda c: c[0])[-limit:]
        return [
            {"ts": int(c[0]) * 1000, "low": float(c[1]), "high": float(c[2]),
             "open": float(c[3]), "close": float(c[4]), "volume": float(c[5]), "trades": None}
            for c in rows
        ]

    async def close(self) -> None:
        await self._client.aclose()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_ts(value: str | None) -> int:
    if not value:
        return _now_ms()
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return _now_ms()
