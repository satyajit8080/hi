"""Binance adapter (public market data — no API key required).

Streams used, per the published WebSocket docs:

    <symbol>@depth@100ms     diff depth, drives the local book
    <symbol>@aggTrade        aggregated trades, drives CVD and trade imbalance
    <symbol>@bookTicker      best bid/ask, lowest-latency touch
    <symbol>@forceOrder      liquidations (futures endpoint only)

Rate-limit notes that shaped this implementation:
  * one combined connection carries up to 1024 streams, and 3 symbols x 3
    streams is nowhere near that, so we open exactly one socket per market type;
  * incoming client messages are capped at 5/sec, so subscriptions are sent once
    in a single combined URL rather than as live SUBSCRIBE frames;
  * a connection is only valid for 24h, so a forced disconnect is expected and
    handled by the reconnect loop rather than treated as an error;
  * `/api/v3/depth` weight rises steeply with `limit`, so we request the
    smallest snapshot that covers our configured depth.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

import httpx
import websockets

from app.market.adapters.base import ExchangeAdapter, MarketEvent

SPOT_WS = "wss://stream.binance.com:9443/stream"
SPOT_REST = "https://api.binance.com"
FUTURES_WS = "wss://fstream.binance.com/stream"
FUTURES_REST = "https://fapi.binance.com"

TF_MAP = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d"}


class BinanceAdapter(ExchangeAdapter):
    name = "binance"
    supports_liquidations = True

    def __init__(self, symbols: list[str], depth: int = 20, futures: bool = False) -> None:
        super().__init__(symbols, depth)
        self.futures = futures
        self.rest_base = FUTURES_REST if futures else SPOT_REST
        self.ws_base = FUTURES_WS if futures else SPOT_WS
        self._client = httpx.AsyncClient(base_url=self.rest_base, timeout=10.0)
        self._backoff = 1.0

    def _stream_url(self) -> str:
        parts: list[str] = []
        for s in self.symbols:
            low = s.lower()
            parts += [f"{low}@depth@100ms", f"{low}@aggTrade", f"{low}@bookTicker"]
            if self.futures:
                parts.append(f"{low}@forceOrder")
        return f"{self.ws_base}?streams={'/'.join(parts)}"

    async def stream(self) -> AsyncIterator[MarketEvent]:
        url = self._stream_url()
        while True:
            try:
                async with websockets.connect(
                    url, ping_interval=20, ping_timeout=20, max_queue=4096
                ) as ws:
                    self._backoff = 1.0
                    yield MarketEvent("heartbeat", self.name, "*", _now_ms(), {"state": "connected"})
                    async for raw in ws:
                        event = self._normalise(json.loads(raw))
                        if event is not None:
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on anything
                yield MarketEvent(
                    "heartbeat", self.name, "*", _now_ms(),
                    {"state": "disconnected", "error": str(exc)[:200]},
                )
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)

    def _normalise(self, msg: dict) -> MarketEvent | None:
        data = msg.get("data") or msg
        etype = data.get("e")
        ts = int(data.get("E") or data.get("T") or _now_ms())

        if etype == "depthUpdate":
            return MarketEvent(
                "book_diff", self.name, data["s"], ts,
                {
                    "first_update_id": int(data["U"]),
                    "final_update_id": int(data["u"]),
                    "prev_update_id": int(data["pu"]) if "pu" in data else None,
                    "bids": [(float(p), float(q)) for p, q in data.get("b", [])],
                    "asks": [(float(p), float(q)) for p, q in data.get("a", [])],
                },
            )

        if etype == "aggTrade":
            return MarketEvent(
                "trade", self.name, data["s"], int(data["T"]),
                {
                    "price": float(data["p"]),
                    "qty": float(data["q"]),
                    "is_buyer_maker": bool(data["m"]),
                    "trade_id": data.get("a"),
                },
            )

        if etype == "forceOrder":
            o = data["o"]
            # A forced SELL closes a long. Report the side that was liquidated.
            return MarketEvent(
                "liquidation", self.name, o["s"], int(o["T"]),
                {
                    "side": "long" if o["S"] == "SELL" else "short",
                    "price": float(o["ap"] or o["p"]),
                    "qty": float(o["q"]),
                },
            )

        if "b" in data and "a" in data and "s" in data and etype is None:
            return MarketEvent(
                "book_snapshot", self.name, data["s"], ts,
                {
                    "best_bid": float(data["b"]),
                    "best_bid_qty": float(data["B"]),
                    "best_ask": float(data["a"]),
                    "best_ask_qty": float(data["A"]),
                    "top_only": True,
                },
            )
        return None

    async def fetch_book_snapshot(self, symbol: str) -> MarketEvent:
        # Weight tiers: 1-100 => 5, 101-500 => 25. Stay in the cheap tier.
        limit = 100 if self.depth <= 100 else 500
        path = "/fapi/v1/depth" if self.futures else "/api/v3/depth"
        r = await self._client.get(path, params={"symbol": symbol, "limit": limit})
        r.raise_for_status()
        payload = r.json()
        return MarketEvent(
            "book_snapshot", self.name, symbol, _now_ms(),
            {
                "last_update_id": int(payload["lastUpdateId"]),
                "bids": [(float(p), float(q)) for p, q in payload["bids"]],
                "asks": [(float(p), float(q)) for p, q in payload["asks"]],
                "top_only": False,
            },
        )

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
        path = "/fapi/v1/klines" if self.futures else "/api/v3/klines"
        r = await self._client.get(
            path, params={"symbol": symbol, "interval": TF_MAP.get(timeframe, "1h"), "limit": limit}
        )
        r.raise_for_status()
        return [
            {
                "ts": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "trades": int(k[8]),
            }
            for k in r.json()
        ]

    async def fetch_funding(self, symbol: str) -> dict | None:
        if not self.futures:
            return None
        r = await self._client.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
        if r.status_code != 200:
            return None
        d = r.json()
        return {
            "funding_rate": float(d.get("lastFundingRate", 0.0)),
            "next_funding_ts": int(d.get("nextFundingTime", 0)),
            "mark_price": float(d.get("markPrice", 0.0)),
        }

    async def fetch_open_interest(self, symbol: str) -> float | None:
        if not self.futures:
            return None
        r = await self._client.get("/fapi/v1/openInterest", params={"symbol": symbol})
        return float(r.json()["openInterest"]) if r.status_code == 200 else None

    async def close(self) -> None:
        await self._client.aclose()


def _now_ms() -> int:
    return int(time.time() * 1000)
