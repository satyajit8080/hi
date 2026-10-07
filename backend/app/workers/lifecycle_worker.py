"""Advances open signals bar by bar and resolves their outcomes.

Reads recent candles for each symbol and replays every bar published after a
signal went live. Uses the conservative intrabar rule in `engine/lifecycle.py`:
when a bar contains both the stop and a target, the stop is recorded first.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.db import SessionLocal, close_connections
from app.engine.lifecycle import Bar, advance, apply_update, load_open_positions, save_excursions
from app.market.adapters.binance import BinanceAdapter
from app.market.adapters.replay import ReplayAdapter
from app.services import alerts

log = logging.getLogger("signalproof.lifecycle")
CYCLE_SECONDS = 30
TRACK_TIMEFRAME = "5m"          # finest resolution we track outcomes on


class LifecycleWorker:
    def __init__(self) -> None:
        self.adapter = (
            ReplayAdapter(settings.symbols, settings.orderbook_depth, speed=settings.replay_speed)
            if settings.is_replay
            else BinanceAdapter(settings.symbols, settings.orderbook_depth)
        )
        self.running = True

    async def run(self) -> None:
        while self.running:
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("lifecycle cycle failed")
            await asyncio.sleep(CYCLE_SECONDS)

    async def cycle(self) -> None:
        async with SessionLocal() as session:
            for symbol in settings.symbols:
                positions = await load_open_positions(session, symbol)
                if not positions:
                    continue

                rows = await self.adapter.fetch_candles(symbol, TRACK_TIMEFRAME, 300)
                if not rows:
                    continue
                bars = [
                    Bar(
                        ts=datetime.fromtimestamp(r["ts"] / 1000, tz=timezone.utc),
                        high=float(r["high"]),
                        low=float(r["low"]),
                        close=float(r["close"]),
                    )
                    for r in rows
                ]

                for pos in positions:
                    # Only bars that opened after publication. Earlier bars contain
                    # prices from before the signal existed and must not resolve it.
                    live_bars = [
                        b for b in bars if pos.published_at is None or b.ts >= pos.published_at
                    ]
                    closed = False
                    for bar in live_bars:
                        update = advance(pos, bar)
                        if not update.events:
                            # Still record excursion drift so MFE/MAE stay honest.
                            pos.mfe_pct = max(pos.mfe_pct, update.mfe_pct / 100.0)
                            pos.mae_pct = min(pos.mae_pct, update.mae_pct / 100.0)
                            continue
                        await apply_update(session, pos, update, bar)
                        pos.status = update.status
                        pos.tp_hits = update.tp_hits
                        pos.mfe_pct = update.mfe_pct / 100.0
                        pos.mae_pct = update.mae_pct / 100.0
                        log.info(
                            "%s -> %s (%s)",
                            pos.signal_id[:8], update.status, update.close_reason or "in progress",
                        )
                        if update.outcome:
                            closed = True
                            break
                    if not closed and live_bars:
                        await save_excursions(session, pos)

            result = await alerts.flush(session)
            if result["processed"]:
                log.info("alerts flushed: %s", result)
            await session.commit()


async def main() -> None:
    logging.basicConfig(level=settings.log_level.upper())
    log.info("lifecycle worker starting")
    try:
        await LifecycleWorker().run()
    finally:
        await close_connections()


if __name__ == "__main__":
    asyncio.run(main())
