"""Local order book maintenance.

Follows the documented Binance procedure, which the futures stream extends with
a `pu` field:

  1. Open the diff-depth stream and buffer events.
  2. Fetch a REST depth snapshot.
  3. Drop buffered events with ``u <= lastUpdateId``.
  4. The first applied event must satisfy ``U <= lastUpdateId + 1 <= u``.
  5. Thereafter each event's ``U`` must equal the previous ``u + 1``
     (futures: each event's ``pu`` must equal the previous ``u``).

Any violation means we missed a message. The only correct response is to mark
the book unsynced and resync from a fresh snapshot — never to patch over the
gap, because a silently wrong book produces confidently wrong order-flow
features, which is worse than no features at all.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.features.microstructure import BookSnapshot, BookPersistenceTracker


@dataclass
class DepthUpdate:
    first_update_id: int          # "U"
    final_update_id: int          # "u"
    prev_update_id: int | None    # "pu" — futures only
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    event_ts_ms: int


class OutOfSync(Exception):
    pass


@dataclass
class LocalOrderBook:
    symbol: str
    venue: str
    depth_limit: int = 20
    is_futures: bool = False

    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    last_update_id: int = 0
    synced: bool = False
    last_event_ms: int = 0
    resync_count: int = 0
    _buffer: list[DepthUpdate] = field(default_factory=list)
    _persistence: BookPersistenceTracker = field(default_factory=BookPersistenceTracker)

    # ── snapshot handling ───────────────────────────────────────────────────

    def apply_snapshot(self, bids, asks, last_update_id: int, ts_ms: int | None = None) -> None:
        self.bids = {float(p): float(q) for p, q in bids if float(q) > 0}
        self.asks = {float(p): float(q) for p, q in asks if float(q) > 0}
        self.last_update_id = int(last_update_id)
        self.last_event_ms = ts_ms or int(time.time() * 1000)
        self.synced = True
        self._drain_buffer()

    def buffer(self, update: DepthUpdate) -> None:
        self._buffer.append(update)
        if len(self._buffer) > 5000:                 # bound memory on a stalled resync
            self._buffer = self._buffer[-2500:]

    def _drain_buffer(self) -> None:
        pending = sorted(self._buffer, key=lambda u: u.final_update_id)
        self._buffer.clear()
        first_applied = False
        for u in pending:
            if u.final_update_id <= self.last_update_id:
                continue                              # already covered by snapshot
            if not first_applied:
                if not (u.first_update_id <= self.last_update_id + 1 <= u.final_update_id):
                    continue                          # snapshot is newer; keep waiting
                first_applied = True
            try:
                self.apply(u)
            except OutOfSync:
                self.synced = False
                return

    # ── incremental updates ─────────────────────────────────────────────────

    def apply(self, u: DepthUpdate) -> None:
        if not self.synced:
            self.buffer(u)
            return

        if self.is_futures:
            if u.prev_update_id is not None and u.prev_update_id != self.last_update_id:
                self.synced = False
                self.resync_count += 1
                raise OutOfSync(
                    f"{self.venue}:{self.symbol} futures gap — pu={u.prev_update_id} "
                    f"but local last_update_id={self.last_update_id}"
                )
        else:
            if u.final_update_id <= self.last_update_id:
                return                                # stale replay, ignore
            if u.first_update_id > self.last_update_id + 1:
                self.synced = False
                self.resync_count += 1
                raise OutOfSync(
                    f"{self.venue}:{self.symbol} spot gap — U={u.first_update_id} "
                    f"but expected <= {self.last_update_id + 1}"
                )

        for price, qty in u.bids:
            if qty == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty
        for price, qty in u.asks:
            if qty == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty

        self.last_update_id = u.final_update_id
        self.last_event_ms = u.event_ts_ms
        self._trim()
        self._persistence.update(self.snapshot())

    def _trim(self, keep: int = 400) -> None:
        """Bound memory. We only ever read the top `depth_limit` levels."""
        if len(self.bids) > keep:
            self.bids = dict(sorted(self.bids.items(), key=lambda kv: -kv[0])[:keep])
        if len(self.asks) > keep:
            self.asks = dict(sorted(self.asks.items(), key=lambda kv: kv[0])[:keep])

    # ── reads ───────────────────────────────────────────────────────────────

    def snapshot(self, levels: int | None = None) -> BookSnapshot:
        n = levels or self.depth_limit
        bids = sorted(self.bids.items(), key=lambda kv: -kv[0])[:n]
        asks = sorted(self.asks.items(), key=lambda kv: kv[0])[:n]
        return BookSnapshot(ts_ms=self.last_event_ms, bids=bids, asks=asks, synced=self.synced)

    def persistent_imbalance(self, levels: int = 20) -> float:
        return self._persistence.persistent_imbalance(self.snapshot(levels), levels)

    def staleness_ms(self, now_ms: int | None = None) -> int:
        now = now_ms or int(time.time() * 1000)
        return max(0, now - self.last_event_ms) if self.last_event_ms else 10**9

    def health(self, max_staleness_ms: int) -> dict:
        snap = self.snapshot()
        stale = self.staleness_ms()
        crossed = snap.is_crossed()
        ok = self.synced and stale <= max_staleness_ms and not crossed and bool(snap.bids and snap.asks)
        flags: list[str] = []
        if not self.synced:
            flags.append("book_out_of_sync")
        if stale > max_staleness_ms:
            flags.append(f"book_stale_{stale}ms")
        if crossed:
            flags.append("book_crossed")
        if not snap.bids or not snap.asks:
            flags.append("book_empty")
        return {
            "venue": self.venue,
            "symbol": self.symbol,
            "synced": self.synced,
            "staleness_ms": stale,
            "crossed": crossed,
            "resyncs": self.resync_count,
            "status": "live" if ok else ("degraded" if self.synced else "stale"),
            "flags": flags,
        }

    def invalidate(self, reason: str = "manual") -> None:
        self.synced = False
        self.resync_count += 1
        self._buffer.clear()
