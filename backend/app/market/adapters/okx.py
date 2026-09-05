"""OKX adapter (public market data, no key).

Streams: `books` (400-level depth with seqId/prevSeqId) and `trades`.
Sequence rule per OKX docs: each update's prevSeqId must equal the last seqId
we applied; the first message after subscribing is a full snapshot with
prevSeqId = -1. A mismatch means a missed message, so the book resyncs.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

import httpx
import websockets

from app.market.adapters.base import ExchangeAdapter, MarketEvent

WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
REST_URL = "https://www.okx.com"
BAR = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1D"}


class OKXAdapter(ExchangeAdapter):
    name = "okx"

    def __init__(self, symbols: list[str], depth: int = 20) -> None:
        super().__init__(symbols, depth)
        self._client = httpx.AsyncClient(base_url=REST_URL, timeout=10.0)
        self._backoff = 1.0

    def to_venue_symbol(self, symbol: str) -> str:
        return symbol.replace("USDT", "-USDT")

    def from_venue_symbol(self, symbol: str) -> str:
        return symbol.replace("-", "")

    async def stream(self) -> AsyncIterator[MarketEvent]:
        args = []
        for s in self.symbols:
            inst = self.to_venue_symbol(s)
            args += [{"channel": "books", "instId": inst}, {"channel": "trades", "instId": inst}]
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=None, max_queue=4096) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    self._backoff = 1.0
                    yield MarketEvent("heartbeat", self.name, "*", _now_ms(), {"state": "connected"})
                    last_ping = time.time()
                    while True:
                        if time.time() - last_ping > 25:
                            await ws.send("ping")           # OKX expects a literal ping frame
                            last_ping = time.time()
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            continue
                        if raw == "pong":
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
        out: list[MarketEvent] = []
        arg = msg.get("arg") or {}
        channel = arg.get("channel")
        symbol = self.from_venue_symbol(arg.get("instId", ""))
        for d in msg.get("data", []):
            if channel == "books":
                seq = int(d.get("seqId", 0))
                prev = int(d.get("prevSeqId", -1))
                ts = int(d.get("ts", _now_ms()))
                bids = [(float(b[0]), float(b[1])) for b in d.get("bids", [])]
                asks = [(float(a[0]), float(a[1])) for a in d.get("asks", [])]
                if msg.get("action") == "snapshot" or prev == -1:
                    out.append(MarketEvent("book_snapshot", self.name, symbol, ts,
                                           {"last_update_id": seq, "bids": bids, "asks": asks,
                                            "top_only": False}))
                else:
                    out.append(MarketEvent("book_diff", self.name, symbol, ts,
                                           {"first_update_id": prev + 1, "final_update_id": seq,
                                            "prev_update_id": prev, "bids": bids, "asks": asks}))
            elif channel == "trades":
                out.append(MarketEvent("trade", self.name, symbol, int(d.get("ts", _now_ms())), {
                    "price": float(d["px"]),
                    "qty": float(d["sz"]),
                    # OKX `side` is the taker side: a taker sell hit a resting bid.
                    "is_buyer_maker": d.get("side") == "sell",
                    "trade_id": d.get("tradeId"),
                }))
        return out

    async def fetch_book_snapshot(self, symbol: str) -> MarketEvent:
        r = await self._client.get("/api/v5/market/books",
                                   params={"instId": self.to_venue_symbol(symbol), "sz": 50})
        r.raise_for_status()
        d = r.json()["data"][0]
        # REST carries no seqId; the WS snapshot that follows subscription
        # replaces this and establishes the sequence.
        return MarketEvent("book_snapshot", self.name, symbol, int(d.get("ts", _now_ms())), {
            "last_update_id": 0,
            "bids": [(float(b[0]), float(b[1])) for b in d["bids"]],
            "asks": [(float(a[0]), float(a[1])) for a in d["asks"]],
            "top_only": False,
        })

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int = 300) -> list[dict]:
        r = await self._client.get("/api/v5/market/candles", params={
            "instId": self.to_venue_symbol(symbol), "bar": BAR.get(timeframe, "1H"), "limit": min(limit, 300),
        })
        if r.status_code != 200:
            return []
        rows = sorted(r.json().get("data", []), key=lambda c: int(c[0]))
        return [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]),
                 "close": float(c[4]), "volume": float(c[5]), "trades": None} for c in rows]

    async def close(self) -> None:
        await self._client.aclose()


def _now_ms() -> int:
    return int(time.time() * 1000)
