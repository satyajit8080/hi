"""Market data ingestion.

One task per venue, all feeding a shared `MarketHub`. Responsibilities:

  * keep local books synchronised, and resync from a REST snapshot the moment a
    sequence gap is detected rather than patching over it;
  * roll derived microstructure metrics into TimescaleDB on a fixed cadence —
    derived only, never raw L2, because full depth history is gigabytes a day
    per symbol and we would never read most of it;
  * publish market state and feed health to Redis for the API and the UI.

A venue task that dies is restarted with backoff. A venue that stays down simply
lowers `venues_live`, which is what makes the engine stop publishing.
"""
from __future__ import annotations

import asyncio
import logging
import signal as os_signal

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal, close_connections, get_redis
from app.market.adapters.base import ExchangeAdapter
from app.market.adapters.binance import BinanceAdapter
from app.market.adapters.bybit import BybitAdapter
from app.market.adapters.coinbase import CoinbaseAdapter
from app.market.adapters.okx import OKXAdapter
from app.market.adapters.replay import ReplayAdapter
from app.market.hub import MarketHub

log = logging.getLogger("signalproof.ingest")

PERSIST_INTERVAL_S = 15
PUBLISH_INTERVAL_S = 1.0


def build_adapters() -> list[ExchangeAdapter]:
    symbols = settings.symbols
    depth = settings.orderbook_depth

    if settings.is_replay:
        # One replay stream per configured venue so cross-venue corroboration
        # logic exercises the same code path it would in live mode.
        return [
            ReplayAdapter(symbols, depth, speed=settings.replay_speed, venue_label=v)
            for v in settings.venues
        ]

    adapters: list[ExchangeAdapter] = []
    for venue in settings.venues:
        if venue == "binance":
            adapters.append(BinanceAdapter(symbols, depth))
        elif venue == "coinbase":
            adapters.append(CoinbaseAdapter(symbols, depth))
        elif venue == "okx":
            adapters.append(OKXAdapter(symbols, depth))
        elif venue == "bybit":
            adapters.append(BybitAdapter(symbols, depth))
        else:
            log.warning("no adapter for venue %s — skipping", venue)
    return adapters


class Ingestor:
    def __init__(self) -> None:
        self.hub = MarketHub(settings.symbols, settings.venues, settings.orderbook_depth)
        self.adapters = build_adapters()
        self.running = True

    async def run(self) -> None:
        tasks = [asyncio.create_task(self._venue_loop(a)) for a in self.adapters]
        tasks.append(asyncio.create_task(self._publish_loop()))
        tasks.append(asyncio.create_task(self._persist_loop()))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for a in self.adapters:
                await a.close()
            await close_connections()

    async def _venue_loop(self, adapter: ExchangeAdapter) -> None:
        venue = adapter.venue_label if isinstance(adapter, ReplayAdapter) else adapter.name
        backoff = 1.0
        while self.running:
            if self.hub.circuits.get(venue) and self.hub.circuits[venue].is_open():
                await asyncio.sleep(5.0)          # cooling down; venue counts as down
                continue
            try:
                await self._seed_books(adapter, venue)
                async for event in adapter.stream():
                    self.hub.apply(event)
                    st = self.hub.state.get((event.venue, event.symbol))
                    if st is not None and not st.book.synced and event.kind == "book_diff":
                        await self._resync(adapter, venue, event.symbol)
                    backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                tripped = self.hub.record_failure(venue)
                log.exception("%s stream failed (circuit %s); retrying in %.0fs",
                              venue, "OPEN" if tripped else "closed", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _seed_books(self, adapter: ExchangeAdapter, venue: str) -> None:
        for symbol in settings.symbols:
            await self._resync(adapter, venue, symbol)

    async def _resync(self, adapter: ExchangeAdapter, venue: str, symbol: str) -> None:
        try:
            snap = await adapter.fetch_book_snapshot(symbol)
            snap.venue = venue
            self.hub.apply(snap)
            log.info("book resynced %s:%s", venue, symbol)
        except Exception as exc:  # noqa: BLE001
            self.hub.record_failure(venue)
            log.warning("resync failed for %s:%s — %s", venue, symbol, exc)
            await asyncio.sleep(1.0)

    async def _publish_loop(self) -> None:
        redis = get_redis()
        while self.running:
            try:
                await self.hub.publish_to_redis(redis)
            except Exception:  # noqa: BLE001
                log.exception("failed to publish market state")
            await asyncio.sleep(PUBLISH_INTERVAL_S)

    async def _persist_loop(self) -> None:
        """Roll derived metrics into TimescaleDB. Raw depth is never written."""
        while self.running:
            await asyncio.sleep(PERSIST_INTERVAL_S)
            try:
                async with SessionLocal() as session:
                    for symbol in settings.symbols:
                        micro = self.hub.micro_features(symbol)
                        if not micro.available:
                            continue
                        await session.execute(
                            text(
                                """
                                INSERT INTO microstructure (
                                    venue, symbol, ts, best_bid, best_ask, mid, micro_price,
                                    spread_abs, spread_rel, spread_z, obi_5, obi_20,
                                    obi_persistent, ofi, ofi_z, cvd_spot, cvd_perp,
                                    trade_imbalance, large_print_z, book_synced, is_simulated
                                ) VALUES (
                                    :venue, :symbol, now(), :best_bid, :best_ask, :mid, :micro_price,
                                    :spread_abs, :spread_rel, :spread_z, :obi_5, :obi_20,
                                    :obi_persistent, :ofi, :ofi_z, :cvd_spot, :cvd_perp,
                                    :trade_imbalance, :large_print_z, :book_synced, :is_simulated
                                )
                                ON CONFLICT (venue, symbol, ts) DO NOTHING
                                """
                            ),
                            {
                                "venue": micro.venue,
                                "symbol": symbol,
                                "best_bid": _n(micro.best_bid),
                                "best_ask": _n(micro.best_ask),
                                "mid": _n(micro.mid),
                                "micro_price": _n(micro.micro_price),
                                "spread_abs": _n(micro.best_ask - micro.best_bid),
                                "spread_rel": _n(micro.spread_rel),
                                "spread_z": _n(micro.spread_z),
                                "obi_5": _n(micro.obi_5),
                                "obi_20": _n(micro.obi_20),
                                "obi_persistent": _n(micro.obi_persistent),
                                "ofi": _n(micro.ofi),
                                "ofi_z": _n(micro.ofi_z),
                                "cvd_spot": _n(micro.cvd_spot),
                                "cvd_perp": _n(micro.cvd_perp),
                                "trade_imbalance": _n(micro.trade_imbalance),
                                "large_print_z": _n(micro.large_print_z),
                                "book_synced": micro.book_synced,
                                "is_simulated": settings.is_replay,
                            },
                        )

                    health = self.hub.health()
                    for venue, status in health["venues"].items():
                        await session.execute(
                            text(
                                """
                                INSERT INTO feed_health (venue, stream, last_event_ts, staleness_ms,
                                                         book_synced, status, detail)
                                VALUES (:venue, 'aggregate', now(), :staleness, :synced, :status, :detail)
                                ON CONFLICT (venue, stream) DO UPDATE
                                    SET ts = now(), last_event_ts = now(),
                                        staleness_ms = EXCLUDED.staleness_ms,
                                        book_synced = EXCLUDED.book_synced,
                                        status = EXCLUDED.status, detail = EXCLUDED.detail
                                """
                            ),
                            {
                                "venue": venue,
                                "staleness": int(health["max_staleness_ms"]),
                                "synced": status["books_synced"] > 0,
                                "status": "live" if status["connected"] else "down",
                                "detail": ", ".join(health["flags"][:5]) or None,
                            },
                        )
                    await session.commit()
            except Exception:  # noqa: BLE001
                log.exception("persist loop failed")


def _n(value):
    try:
        f = float(value)
        return None if f != f or f in (float("inf"), float("-inf")) else f
    except (TypeError, ValueError):
        return None


async def main() -> None:
    logging.basicConfig(level=settings.log_level.upper())
    ingestor = Ingestor()
    loop = asyncio.get_running_loop()

    def stop() -> None:
        ingestor.running = False
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (os_signal.SIGINT, os_signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop)
        except NotImplementedError:
            pass

    log.info("ingest starting — mode=%s venues=%s", settings.market_mode, settings.venues)
    await ingestor.run()


if __name__ == "__main__":
    asyncio.run(main())
