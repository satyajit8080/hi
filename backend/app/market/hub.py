"""In-memory market state shared by the ingest worker and the API.

Holds, per (venue, symbol): the synchronised book, a rolling trade window, CVD
accumulators, OFI history and feed health. Publishes a compact snapshot to Redis
so the API and WebSocket layer can serve it without touching the ingest loop.

The health view here is what produces DATA DELAYED in the UI and what stops the
engine from publishing on bad data.
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from app.config import settings
from app.features.microstructure import (
    BookSnapshot,
    CVDAccumulator,
    Trade,
    cvd_divergence,
    large_print_score,
    micro_price,
    order_book_imbalance,
    order_flow_imbalance,
    queue_imbalance,
    trade_flow_imbalance,
    weighted_order_book_imbalance,
)
from app.features.snapshot import DataQuality, MicroFeatures
from app.market.adapters.base import MarketEvent
from app.market.orderbook import DepthUpdate, LocalOrderBook, OutOfSync

# Venues whose depth updates carry a previous-sequence id and get the strict check.
STRICT_SEQ_VENUES = ("okx",)
REDIS_MARKET_KEY = "sp:market:{symbol}"
REDIS_HEALTH_KEY = "sp:health"


@dataclass
class VenueSymbolState:
    venue: str
    symbol: str
    book: LocalOrderBook
    cvd_spot: CVDAccumulator = field(default_factory=lambda: CVDAccumulator(reset_interval_ms=86_400_000))
    cvd_perp: CVDAccumulator = field(default_factory=lambda: CVDAccumulator(reset_interval_ms=86_400_000))
    trades: deque[Trade] = field(default_factory=lambda: deque(maxlen=3000))
    ofi_history: deque[float] = field(default_factory=lambda: deque(maxlen=600))
    spread_history: deque[float] = field(default_factory=lambda: deque(maxlen=600))
    notional_history: deque[float] = field(default_factory=lambda: deque(maxlen=1500))
    price_history: deque[float] = field(default_factory=lambda: deque(maxlen=600))
    cvd_history: deque[float] = field(default_factory=lambda: deque(maxlen=600))
    last_book: BookSnapshot | None = None
    last_trade_ms: int = 0
    connected: bool = False
    last_error: str | None = None
    is_simulated: bool = False
    # Duplicate detection: a reconnect replays recent trades, and double-counting
    # them would bias CVD in whatever direction the last minute happened to run.
    seen_trade_ids: deque = field(default_factory=lambda: deque(maxlen=4000))
    seen_trade_set: set = field(default_factory=set)
    duplicates: int = 0
    # Timestamp validation: exchange event time against our clock.
    skew_samples: deque = field(default_factory=lambda: deque(maxlen=200))
    # Anti-spoof accounting over the recent window.
    level_adds: deque = field(default_factory=lambda: deque(maxlen=2000))     # (ts_ms, count)
    level_removes: deque = field(default_factory=lambda: deque(maxlen=2000))
    executed_notional: deque = field(default_factory=lambda: deque(maxlen=3000))  # (ts_ms, usd)

    def clock_skew_ms(self) -> int:
        return int(max(self.skew_samples)) if self.skew_samples else 0


@dataclass
class VenueCircuit:
    """Circuit breaker per venue.

    A venue that keeps failing gets a cooldown instead of a hot retry loop. While
    open, the venue counts as down, which lowers `venues_live` and stops the
    engine from publishing on what is left.
    """

    threshold: int = 5
    window_s: float = 60.0
    cooldown_s: float = 120.0
    failures: deque = field(default_factory=lambda: deque(maxlen=50))
    open_until: float = 0.0
    trips: int = 0

    def record_failure(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        self.failures.append(now)
        recent = [t for t in self.failures if now - t <= self.window_s]
        if len(recent) >= self.threshold and now >= self.open_until:
            self.open_until = now + self.cooldown_s
            self.trips += 1
            return True
        return False

    def is_open(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return now < self.open_until

    def reset(self) -> None:
        self.failures.clear()
        self.open_until = 0.0


class MarketHub:
    def __init__(self, symbols: list[str], venues: list[str], depth: int = 20) -> None:
        self.symbols = symbols
        self.venues = venues
        self.depth = depth
        self.state: dict[tuple[str, str], VenueSymbolState] = {
            (v, s): VenueSymbolState(v, s, LocalOrderBook(s, v, depth, is_futures=(v in STRICT_SEQ_VENUES)))
            for v in venues
            for s in symbols
        }
        self.last_price: dict[str, float] = {}
        self.liquidations: dict[str, deque] = {s: deque(maxlen=500) for s in symbols}
        self.circuits: dict[str, VenueCircuit] = {v: VenueCircuit() for v in venues}
        self.max_divergence_pct = 0.5   # cross-venue mid-price tolerance

    # ── failover ────────────────────────────────────────────────────────────

    def venue_is_live(self, venue: str, symbol: str) -> bool:
        st = self.state.get((venue, symbol))
        if st is None or not st.connected or self.circuits[venue].is_open():
            return False
        if not st.book.synced or st.book.staleness_ms() > settings.max_book_staleness_ms:
            return False
        return bool(st.last_trade_ms) and (_now_ms() - st.last_trade_ms) <= settings.max_trade_staleness_ms

    def effective_primary(self, symbol: str) -> str:
        """Primary venue if healthy, else the first healthy corroborating venue."""
        if self.venue_is_live(settings.primary_venue, symbol):
            return settings.primary_venue
        for v in self.venues:
            if self.venue_is_live(v, symbol):
                return v
        return settings.primary_venue

    def record_failure(self, venue: str) -> bool:
        return self.circuits[venue].record_failure() if venue in self.circuits else False

    # ── event application ───────────────────────────────────────────────────

    def apply(self, ev: MarketEvent) -> None:
        if ev.kind == "heartbeat":
            for (venue, _), st in self.state.items():
                if venue == ev.venue or ev.symbol == "*":
                    st.connected = ev.payload.get("state") == "connected"
                    st.last_error = ev.payload.get("error")
                    if not st.connected:
                        st.book.invalidate("disconnect")
            return

        key = (ev.venue, ev.symbol)
        st = self.state.get(key)
        if st is None:
            return
        st.is_simulated = st.is_simulated or ev.is_simulated
        if ev.kind in ("trade", "book_diff") and not ev.is_simulated:
            st.skew_samples.append(abs(_now_ms() - ev.ts_ms))

        if ev.kind == "book_snapshot":
            if ev.payload.get("top_only"):
                # bookTicker: refresh the touch without disturbing the book.
                st.last_book = BookSnapshot(
                    ts_ms=ev.ts_ms,
                    bids=[(ev.payload["best_bid"], ev.payload["best_bid_qty"])],
                    asks=[(ev.payload["best_ask"], ev.payload["best_ask_qty"])],
                    synced=st.book.synced,
                )
            else:
                st.book.apply_snapshot(
                    ev.payload["bids"], ev.payload["asks"],
                    ev.payload["last_update_id"], ev.ts_ms,
                )
                self._on_book_change(st)

        elif ev.kind == "book_diff":
            removes = sum(1 for _, q in ev.payload["bids"] if q == 0) + sum(1 for _, q in ev.payload["asks"] if q == 0)
            adds = len(ev.payload["bids"]) + len(ev.payload["asks"]) - removes
            if adds:
                st.level_adds.append((ev.ts_ms, adds))
            if removes:
                st.level_removes.append((ev.ts_ms, removes))
            update = DepthUpdate(
                first_update_id=ev.payload["first_update_id"],
                final_update_id=ev.payload["final_update_id"],
                prev_update_id=ev.payload.get("prev_update_id"),
                bids=ev.payload["bids"],
                asks=ev.payload["asks"],
                event_ts_ms=ev.ts_ms,
            )
            try:
                st.book.apply(update)
                self._on_book_change(st)
            except OutOfSync as exc:
                st.last_error = str(exc)   # ingest worker will resync

        elif ev.kind == "trade":
            tid = ev.payload.get("trade_id")
            if tid is not None:
                if tid in st.seen_trade_set:
                    st.duplicates += 1
                    return
                if len(st.seen_trade_ids) == st.seen_trade_ids.maxlen:
                    st.seen_trade_set.discard(st.seen_trade_ids[0])
                st.seen_trade_ids.append(tid)
                st.seen_trade_set.add(tid)
            trade = Trade(ev.ts_ms, ev.payload["price"], ev.payload["qty"],
                          bool(ev.payload["is_buyer_maker"]))
            st.executed_notional.append((ev.ts_ms, trade.notional))
            st.trades.append(trade)
            st.notional_history.append(trade.notional)
            st.last_trade_ms = ev.ts_ms
            value = st.cvd_spot.add(trade)
            st.cvd_history.append(value)
            st.price_history.append(trade.price)
            if ev.venue == self.effective_primary(ev.symbol):
                self.last_price[ev.symbol] = trade.price

        elif ev.kind == "liquidation":
            self.liquidations[ev.symbol].append({
                "ts": ev.ts_ms,
                "side": ev.payload["side"],
                "price": ev.payload["price"],
                "qty": ev.payload["qty"],
                "notional": ev.payload["price"] * ev.payload["qty"],
                "venue": ev.venue,
            })

    def _on_book_change(self, st: VenueSymbolState) -> None:
        snap = st.book.snapshot()
        if not snap.bids or not snap.asks:
            return
        if st.last_book is not None and len(st.last_book.bids) and len(st.last_book.asks):
            st.ofi_history.append(order_flow_imbalance(st.last_book, snap))
        st.spread_history.append(snap.spread_rel)
        st.last_book = snap

    # ── feature extraction ──────────────────────────────────────────────────

    def spoof_metrics(self, st: VenueSymbolState, window_ms: int = 60_000) -> tuple[float, float, float]:
        """(cancel_rate, displayed_vs_executed, spoof_risk).

        Cancel rate approaching 0.5 means levels are being placed and pulled in
        lockstep rather than resting and filling. Displayed-vs-executed far above 1 means large size is
        on display that nothing is trading into. Either alone is common in a
        quiet market; both together is the spoofing fingerprint.
        """
        now = _now_ms()
        adds = sum(c for t, c in st.level_adds if now - t <= window_ms)
        removes = sum(c for t, c in st.level_removes if now - t <= window_ms)
        cancel_rate = removes / (adds + removes) if (adds + removes) > 0 else 0.0
        executed = sum(u for t, u in st.executed_notional if now - t <= window_ms)
        snap = st.book.snapshot(5)
        displayed = sum(p * q for p, q in snap.bids) + sum(p * q for p, q in snap.asks)
        ratio = displayed / executed if executed > 0 else (10.0 if displayed > 0 else 0.0)
        # Every removal is preceded by an add, so a pure place-and-pull cycle
        # tops out near cancel_rate 0.5; healthy books sit well below 0.3 because
        # most updates are size changes on resting levels. Penalise from 0.30.
        risk = float(np.clip(
            0.5 * max(0.0, min((cancel_rate - 0.30) / 0.25, 1.0))
            + 0.5 * max(0.0, min((ratio - 3.0) / 12.0, 1.0)),
            0.0, 1.0,
        ))
        return cancel_rate, min(ratio, 100.0), risk

    def micro_features(self, symbol: str, window_ms: int = 60_000) -> MicroFeatures:
        venue = self.effective_primary(symbol)
        primary = self.state.get((venue, symbol))
        if primary is None or primary.last_book is None:
            return MicroFeatures(venue=venue, ts_ms=_now_ms(), available=False)

        snap = primary.book.snapshot()
        if not snap.bids or not snap.asks:
            return MicroFeatures(venue=primary.venue, ts_ms=_now_ms(), available=False)

        now = _now_ms()
        recent = [t for t in primary.trades if now - t.ts_ms <= window_ms]
        spread_arr = np.array([s for s in primary.spread_history if np.isfinite(s)], dtype=float)
        ofi_arr = np.array(list(primary.ofi_history), dtype=float)

        obi5 = order_book_imbalance(snap, 5)
        agreeing = self._venues_agreeing(symbol, obi5)
        cancel_rate, dve, spoof_risk = self.spoof_metrics(primary, window_ms)

        return MicroFeatures(
            venue=primary.venue,
            ts_ms=snap.ts_ms,
            best_bid=snap.best_bid,
            best_ask=snap.best_ask,
            mid=snap.mid,
            micro_price=micro_price(snap),
            spread_rel=snap.spread_rel,
            spread_z=_robust_z(snap.spread_rel, spread_arr),
            obi_5=obi5,
            obi_20=weighted_order_book_imbalance(snap, self.depth),
            obi_persistent=primary.book.persistent_imbalance(self.depth),
            queue_imbalance=queue_imbalance(snap),
            ofi=float(ofi_arr[-20:].sum()) if len(ofi_arr) else 0.0,
            ofi_z=_robust_z(float(ofi_arr[-20:].sum()) if len(ofi_arr) else 0.0, ofi_arr),
            cvd_spot=primary.cvd_spot.value,
            cvd_perp=primary.cvd_perp.value,
            cvd_divergence=cvd_divergence(list(primary.price_history), list(primary.cvd_history)),
            trade_imbalance=trade_flow_imbalance(recent),
            large_print_z=large_print_score(recent, list(primary.notional_history)),
            venues_agreeing=agreeing,
            cancel_rate=cancel_rate,
            displayed_vs_executed=dve,
            spoof_risk=spoof_risk,
            book_synced=primary.book.synced,
            available=True,
        )

    def _venues_agreeing(self, symbol: str, reference_obi: float) -> int:
        """How many live venues show imbalance on the same side.

        Cross-venue corroboration is the cheapest spoof defence available: one
        book is easy to paint, three are not.
        """
        count = 0
        for venue in self.venues:
            st = self.state.get((venue, symbol))
            if st is None or not st.book.synced:
                continue
            snap = st.book.snapshot()
            if not snap.bids or not snap.asks:
                continue
            obi = order_book_imbalance(snap, 5)
            if abs(reference_obi) < 0.05 or obi * reference_obi > 0:
                count += 1
        return count

    # ── health ──────────────────────────────────────────────────────────────

    def health(self) -> dict:
        now = _now_ms()
        venue_status: dict[str, dict] = {}
        flags: list[str] = []
        worst_staleness = 0

        for venue in self.venues:
            books_ok = 0
            trades_ok = 0
            for symbol in self.symbols:
                st = self.state[(venue, symbol)]
                book_health = st.book.health(settings.max_book_staleness_ms)
                trade_stale = now - st.last_trade_ms if st.last_trade_ms else 10**9
                worst_staleness = max(
                    worst_staleness, min(book_health["staleness_ms"], 10**8), min(trade_stale, 10**8)
                )
                if book_health["status"] == "live":
                    books_ok += 1
                else:
                    flags.extend(f"{venue}:{symbol}:{f}" for f in book_health["flags"])
                if trade_stale <= settings.max_trade_staleness_ms:
                    trades_ok += 1
                else:
                    flags.append(f"{venue}:{symbol}:trades_stale")
            venue_status[venue] = {
                "connected": any(self.state[(venue, s)].connected for s in self.symbols),
                "books_synced": books_ok,
                "trades_fresh": trades_ok,
                "symbols": len(self.symbols),
            }

        worst_skew = 0
        for venue in self.venues:
            if self.circuits[venue].is_open():
                venue_status[venue]["circuit_open"] = True
                venue_status[venue]["connected"] = False
                flags.append(f"{venue}:circuit_open")
            for symbol in self.symbols:
                skew = self.state[(venue, symbol)].clock_skew_ms()
                worst_skew = max(worst_skew, skew)
                if skew > settings.max_clock_skew_ms:
                    flags.append(f"{venue}:{symbol}:clock_skew_{skew}ms")

        divergence = self.price_divergence()
        for symbol, pct in divergence.items():
            if pct > self.max_divergence_pct:
                flags.append(f"{symbol}:venue_price_divergence_{pct:.2f}pct")

        live_venues = sum(
            1
            for v, s in venue_status.items()
            if s["connected"] and s["books_synced"] >= 1 and s["trades_fresh"] >= 1
        )
        degraded = (
            live_venues < settings.min_live_venues
            or worst_staleness > settings.max_trade_staleness_ms
            or worst_skew > settings.max_clock_skew_ms
            or any(p > self.max_divergence_pct for p in divergence.values())
        )

        return {
            "venues": venue_status,
            "venues_live": live_venues,
            "min_venues_required": settings.min_live_venues,
            "max_staleness_ms": int(worst_staleness),
            "clock_skew_ms": int(worst_skew),
            "price_divergence_pct": {k: round(v, 4) for k, v in divergence.items()},
            "duplicates_dropped": sum(st.duplicates for st in self.state.values()),
            "circuit_trips": {v: c.trips for v, c in self.circuits.items()},
            "degraded": degraded,
            "flags": sorted(set(flags))[:20],
            "mode": settings.market_mode,
            "is_simulated": settings.is_replay,
            "checked_at": now,
        }

    def price_divergence(self) -> dict[str, float]:
        """Max relative gap between venue mid prices, per symbol, in percent.

        A feed that is quietly stale, or a venue that has come unpinned from the
        market, shows up here before it shows up anywhere else.
        """
        out: dict[str, float] = {}
        for symbol in self.symbols:
            mids = []
            for venue in self.venues:
                st = self.state[(venue, symbol)]
                if st.book.synced and st.last_book and st.last_book.bids and st.last_book.asks:
                    m = st.book.snapshot(1).mid
                    if np.isfinite(m) and m > 0:
                        mids.append(m)
            if len(mids) >= 2:
                out[symbol] = (max(mids) - min(mids)) / min(mids) * 100.0
        return out

    def data_quality(self) -> DataQuality:
        h = self.health()
        return DataQuality(
            venues_live=h["venues_live"],
            book_synced=all(
                self.state[(self.effective_primary(s), s)].book.synced for s in self.symbols
            ),
            max_staleness_ms=h["max_staleness_ms"],
            clock_skew_ms=h["clock_skew_ms"],
            degraded=h["degraded"],
            flags=h["flags"],
        )

    # ── publishing ──────────────────────────────────────────────────────────

    def public_snapshot(self, symbol: str) -> dict:
        micro = self.micro_features(symbol)
        primary = self.state.get((self.effective_primary(symbol), symbol))
        book = primary.book.snapshot(15) if primary else None
        liqs = list(self.liquidations.get(symbol, []))[-40:]
        return {
            "symbol": symbol,
            "price": self.last_price.get(symbol),
            "book": {
                "bids": [[p, q] for p, q in (book.bids if book else [])],
                "asks": [[p, q] for p, q in (book.asks if book else [])],
                "synced": bool(book.synced) if book else False,
            },
            "micro": {
                k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                for k, v in micro.to_dict().items()
            },
            "liquidations": liqs,
            "source_venue": self.effective_primary(symbol),
            "cvd_series": list(primary.cvd_history)[-180:] if primary else [],
            "price_series": list(primary.price_history)[-180:] if primary else [],
            "is_simulated": settings.is_replay,
            "ts": _now_ms(),
        }

    async def publish_to_redis(self, redis) -> None:
        for symbol in self.symbols:
            await redis.set(
                REDIS_MARKET_KEY.format(symbol=symbol),
                json.dumps(self.public_snapshot(symbol)),
                ex=30,
            )
        await redis.set(REDIS_HEALTH_KEY, json.dumps(self.health()), ex=30)


def _robust_z(value: float, history: np.ndarray) -> float:
    if history is None or len(history) < 20 or not np.isfinite(value):
        return 0.0
    med = float(np.median(history))
    mad = float(np.median(np.abs(history - med)))
    scale = 1.4826 * mad
    return 0.0 if scale < 1e-12 else float((value - med) / scale)


def _now_ms() -> int:
    return int(time.time() * 1000)
