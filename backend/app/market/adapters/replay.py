"""Replay adapter — runs the entire platform without touching an exchange.

Everything it emits carries ``is_simulated=True``. That flag rides through the
feature snapshot, into the signal row, into the API response, and onto a badge
in the UI. Simulated signals are excluded from every published performance
statistic by default, and the performance endpoints say so explicitly.

This exists so the product can be developed, demoed and tested end to end
without ever presenting fabricated numbers as a live track record.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import AsyncIterator

from app.market.adapters.base import ExchangeAdapter, MarketEvent

DATA_DIR = Path(__file__).resolve().parents[2] / "seed" / "data"


class ReplayAdapter(ExchangeAdapter):
    name = "replay"
    supports_liquidations = True

    def __init__(
        self,
        symbols: list[str],
        depth: int = 20,
        speed: float = 60.0,
        venue_label: str = "binance",
        data_dir: Path | None = None,
        loop_forever: bool = True,
    ) -> None:
        super().__init__(symbols, depth)
        self.speed = max(speed, 1.0)
        self.venue_label = venue_label
        self.data_dir = data_dir or DATA_DIR
        self.loop_forever = loop_forever
        # The adapter plays the exchange, so it owns the authoritative book and
        # its update-id sequence. Diffs and snapshots are both served from here,
        # which keeps them consistent the way a real venue's REST and WS are.
        self._books: dict[str, dict] = {}

    def _events_path(self) -> Path:
        return self.data_dir / "events.jsonl"

    def _candles_path(self, symbol: str, timeframe: str) -> Path:
        return self.data_dir / f"candles_{symbol}_{timeframe}.json"

    async def stream(self) -> AsyncIterator[MarketEvent]:
        path = self._events_path()
        if not path.exists():
            raise FileNotFoundError(
                f"No replay dataset at {path}. Generate it with:\n"
                "    python -m app.seed.generate_replay"
            )

        yield MarketEvent("heartbeat", self.venue_label, "*", _now_ms(),
                          {"state": "connected", "mode": "replay"}, is_simulated=True)

        while True:
            prev_ts: int | None = None
            with path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if row["symbol"] not in self.symbols and row["symbol"] != "*":
                        continue

                    # Preserve the original spacing, compressed by `speed`.
                    if prev_ts is not None:
                        delta = (row["ts_ms"] - prev_ts) / 1000.0 / self.speed
                        if delta > 0:
                            await asyncio.sleep(min(delta, 2.0))
                    prev_ts = row["ts_ms"]

                    payload = row["payload"]
                    if row["kind"] == "book_diff":
                        payload = self._advance_book(row["symbol"], payload)

                    # Rebase timestamps onto wall clock so staleness checks pass.
                    yield MarketEvent(
                        kind=row["kind"],
                        venue=self.venue_label,
                        symbol=row["symbol"],
                        ts_ms=_now_ms(),
                        payload=payload,
                        is_simulated=True,
                    )
            if not self.loop_forever:
                return

    async def fetch_book_snapshot(self, symbol: str) -> MarketEvent:
        book = self._book(symbol)
        return MarketEvent(
            "book_snapshot", self.venue_label, symbol, _now_ms(),
            {
                "last_update_id": book["seq"],
                "bids": sorted(book["bids"].items(), key=lambda kv: -kv[0]),
                "asks": sorted(book["asks"].items(), key=lambda kv: kv[0]),
                "top_only": False,
            },
            is_simulated=True,
        )

    def _book(self, symbol: str) -> dict:
        """The simulated venue book, built on first use from the bundled shape.

        The bundled book sits at the series' final price, but the event stream
        starts earlier, so the levels are shifted to the stream's first quote.
        """
        if symbol in self._books:
            return self._books[symbol]
        path = self.data_dir / f"book_{symbol}.json"
        d = json.loads(path.read_text()) if path.exists() else {"bids": [], "asks": []}
        bids = {float(p): float(q) for p, q in d["bids"] if float(q) > 0}
        asks = {float(p): float(q) for p, q in d["asks"] if float(q) > 0}
        anchor = self._first_quote(symbol)
        if bids and asks and anchor is not None:
            shift = anchor - (max(bids) + min(asks)) / 2
            bids = {round(p + shift, 8): q for p, q in bids.items()}
            asks = {round(p + shift, 8): q for p, q in asks.items()}
        self._books[symbol] = {"seq": 1, "bids": bids, "asks": asks}
        return self._books[symbol]

    def _first_quote(self, symbol: str) -> float | None:
        path = self._events_path()
        if not path.exists():
            return None
        with path.open() as fh:
            for line in fh:
                row = json.loads(line)
                if row["kind"] == "book_diff" and row["symbol"] == symbol:
                    p = row["payload"]
                    return (float(p["bids"][0][0]) + float(p["asks"][0][0])) / 2
        return None

    def _advance_book(self, symbol: str, payload: dict) -> dict:
        """Apply one recorded diff to the venue book and return it as the venue
        would publish it: next update id, plus removals for any level the new
        quotes cross, so the book stays uncrossed as the price drifts."""
        book = self._book(symbol)
        bids = [(float(p), float(q)) for p, q in payload["bids"]]
        asks = [(float(p), float(q)) for p, q in payload["asks"]]
        top_bid = max((p for p, q in bids if q > 0), default=None)
        low_ask = min((p for p, q in asks if q > 0), default=None)
        if top_bid is not None:
            asks += [(p, 0.0) for p in book["asks"] if p <= top_bid]
        if low_ask is not None:
            bids += [(p, 0.0) for p in book["bids"] if p >= low_ask]

        for side, levels in (("bids", bids), ("asks", asks)):
            for p, q in levels:
                if q == 0:
                    book[side].pop(p, None)
                else:
                    book[side][p] = q
        self._trim(book)

        book["seq"] += 1
        return {
            "first_update_id": book["seq"],
            "final_update_id": book["seq"],
            "prev_update_id": book["seq"] - 1,
            "bids": bids,
            "asks": asks,
        }

    @staticmethod
    def _trim(book: dict, keep: int = 200) -> None:
        if len(book["bids"]) > keep:
            book["bids"] = dict(sorted(book["bids"].items(), key=lambda kv: -kv[0])[:keep])
        if len(book["asks"]) > keep:
            book["asks"] = dict(sorted(book["asks"].items(), key=lambda kv: kv[0])[:keep])

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
        path = self._candles_path(symbol, timeframe)
        if not path.exists():
            return []
        rows = json.loads(path.read_text())
        # Shift the series so the last bar sits at "now": the engine's staleness
        # gates are wall-clock based and would otherwise reject the dataset.
        if rows:
            offset = _now_ms() - rows[-1]["ts"]
            for r in rows:
                r["ts"] += offset
        return rows[-limit:]

    async def fetch_funding(self, symbol: str) -> dict | None:
        path = self.data_dir / f"derivatives_{symbol}.json"
        if not path.exists():
            return None
        d = json.loads(path.read_text())
        return {"funding_rate": d.get("funding_rate", 0.0),
                "next_funding_ts": _now_ms() + 3_600_000,
                "mark_price": d.get("mark_price", 0.0)}

    async def fetch_open_interest(self, symbol: str) -> float | None:
        path = self.data_dir / f"derivatives_{symbol}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text()).get("open_interest")


def _now_ms() -> int:
    return int(time.time() * 1000)
