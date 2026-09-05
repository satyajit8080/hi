"""Bybit adapter (public market data, no key).

Streams: `orderbook.50.<symbol>` (snapshot then deltas, with update id `u`)
and `publicTrade.<symbol>`. Bybit's `u` is sequential per topic; a jump means a
missed delta and the book resyncs. A snapshot with u == 1 signals a service
restart and is applied as a fresh book.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

import httpx
import websockets

from app.market.adapters.base import ExchangeAdapter, MarketEvent

WS_URL = "wss://stream.bybit.com/v5/public/spot"
REST_URL = "https://api.bybit.com"
INTERVAL = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}


class BybitAdapter(ExchangeAdapter):
    name = "bybit"

    def __init__(self, symbols: list[str], depth: int = 20) -> None:
        super().__init__(symbols, depth)
        self._client = httpx.AsyncClient(base_url=REST_URL, timeout=10.0)
        self._backoff = 1.0

    async def stream(self) -> AsyncIterator[MarketEvent]:
        args = [f"orderbook.50.{s}" for s in self.symbols] + [f"publicTrade.{s}" for s in self.symbols]
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=None, max_queue=4096) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    self._backoff = 1.0
                    yield MarketEvent("heartbeat", self.name, "*", _now_ms(), {"state": "connected"})
                    last_ping = time.time()
                    while True:
                        if time.time() - last_ping > 20:
                            await ws.send(json.dumps({"op": "ping"}))
                            last_ping = time.time()
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            continue
                        for ev in self.normalise(json.loads(raw)):
                            yield ev
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                yield MarketEvent("heartbeat", self.name, "*", _now_ms(),
                                  {"state": "disconnected", "error": str(exc)[:200]})
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)

    def normalise(self, msg: dict) -> list[MarketEvent]:
        topic = msg.get("topic", "")
        data = msg.get("data")
        ts = int(msg.get("ts", _now_ms()))
        out: list[MarketEvent] = []
        if topic.startswith("orderbook.") and isinstance(data, dict):
            symbol = data.get("s", topic.split(".")[-1])
            u = int(data.get("u", 0))
            bids = [(float(b[0]), float(b[1])) for b in data.get("b", [])]
            asks = [(float(a[0]), float(a[1])) for a in data.get("a", [])]
            if msg.get("type") == "snapshot" or u == 1:
                out.append(MarketEvent("book_snapshot", self.name, symbol, ts,
                                       {"last_update_id": u, "bids": bids, "asks": asks, "top_only": False}))
            else:
                out.append(MarketEvent("book_diff", self.name, symbol, ts,
                                       {"first_update_id": u, "final_update_id": u,
                                        "prev_update_id": None, "bids": bids, "asks": asks}))
        elif topic.startswith("publicTrade.") and isinstance(data, list):
            for t in data:
                out.append(MarketEvent("trade", self.name, t.get("s", topic.split(".")[-1]),
                                       int(t.get("T", ts)), {
                    "price": float(t["p"]),
                    "qty": float(t["v"]),
                    # `S` is the taker side.
                    "is_buyer_maker": t.get("S") == "Sell",
                    "trade_id": t.get("i"),
                }))
        return out

    async def fetch_book_snapshot(self, symbol: str) -> MarketEvent:
        r = await self._client.get("/v5/market/orderbook",
                                   params={"category": "spot", "symbol": symbol, "limit": 50})
        r.raise_for_status()
        d = r.json()["result"]
        return MarketEvent("book_snapshot", self.name, symbol, int(d.get("ts", _now_ms())), {
            "last_update_id": int(d.get("u", 0)),
            "bids": [(float(b[0]), float(b[1])) for b in d["b"]],
            "asks": [(float(a[0]), float(a[1])) for a in d["a"]],
            "top_only": False,
        })

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
        r = await self._client.get("/v5/market/kline", params={
            "category": "spot", "symbol": symbol, "interval": INTERVAL.get(timeframe, "60"),
            "limit": min(limit, 1000),
        })
        if r.status_code != 200:
            return []
        rows = sorted(r.json().get("result", {}).get("list", []), key=lambda c: int(c[0]))
        return [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]),
                 "close": float(c[4]), "volume": float(c[5]), "trades": None} for c in rows]

    async def close(self) -> None:
        await self._client.aclose()


def _now_ms() -> int:
    return int(time.time() * 1000)
