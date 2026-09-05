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

                    # Rebase timestamps onto wall clock so staleness checks pass.
                    yield MarketEvent(
                        kind=row["kind"],
                        venue=self.venue_label,
                        symbol=row["symbol"],
                        ts_ms=_now_ms(),
                        payload=row["payload"],
                        is_simulated=True,
                    )
            if not self.loop_forever:
                return

    async def fetch_book_snapshot(self, symbol: str) -> MarketEvent:
        path = self.data_dir / f"book_{symbol}.json"
        if path.exists():
            d = json.loads(path.read_text())
        else:
            d = {"last_update_id": 1, "bids": [], "asks": []}
        return MarketEvent(
            "book_snapshot", self.venue_label, symbol, _now_ms(),
            {
                "last_update_id": int(d["last_update_id"]),
                "bids": [(float(p), float(q)) for p, q in d["bids"]],
                "asks": [(float(p), float(q)) for p, q in d["asks"]],
                "top_only": False,
            },
            is_simulated=True,
        )

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
