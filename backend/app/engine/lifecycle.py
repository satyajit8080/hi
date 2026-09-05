"""Signal lifecycle and outcome tracking.

States:

    WATCHING -> SETUP_FORMING -> SIGNAL_GENERATED -> ACTIVE
             -> TP1_HIT -> TP2_HIT -> TP3_HIT
             -> STOPPED | EXPIRED | CANCELLED

Every transition is appended to `signal_events` with a timestamp. `signal_state`
is a cache of that log and can be rebuilt from it at any time.

**Intrabar ambiguity.** When a candle's range contains both the stop and a
target, OHLC alone cannot say which came first. We always assume the stop
was hit first. That is the pessimistic reading and it is the only defensible one
for a published track record — it can understate results but never flatter them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Direction = Literal["LONG", "SHORT"]

TERMINAL = {"STOPPED", "EXPIRED", "CANCELLED", "TP3_HIT"}


@dataclass(slots=True)
class Bar:
    ts: datetime
    high: float
    low: float
    close: float


@dataclass(slots=True)
class SignalPosition:
    signal_id: str
    direction: Direction
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    expires_at: datetime
    status: str
    tp_hits: int
    mfe_pct: float
    mae_pct: float


@dataclass(slots=True)
class LifecycleUpdate:
    events: list[tuple[str, float, str]]     # (event, price, reason)
    status: str
    tp_hits: int
    mfe_pct: float
    mae_pct: float
    outcome: str | None
    close_price: float | None
    close_reason: str | None
    pnl_pct: float | None
    r_multiple: float | None


def _excursion(direction: Direction, entry: float, price: float) -> float:
    move = (price - entry) / entry
    return move if direction == "LONG" else -move


def advance(pos: SignalPosition, bar: Bar) -> LifecycleUpdate:
    """Apply one bar to a live signal and return the resulting transitions."""
    events: list[tuple[str, float, str]] = []
    status = pos.status
    tp_hits = pos.tp_hits
    outcome: str | None = None
    close_price: float | None = None
    close_reason: str | None = None

    favourable = bar.high if pos.direction == "LONG" else bar.low
    adverse = bar.low if pos.direction == "LONG" else bar.high
    mfe = max(pos.mfe_pct, _excursion(pos.direction, pos.entry, favourable))
    mae = min(pos.mae_pct, _excursion(pos.direction, pos.entry, adverse))

    stop_hit = bar.low <= pos.stop_loss if pos.direction == "LONG" else bar.high >= pos.stop_loss
    targets = [(1, pos.tp1), (2, pos.tp2), (3, pos.tp3)]
    hits = [
        n
        for n, level in targets
        if (bar.high >= level if pos.direction == "LONG" else bar.low <= level)
    ]

    # Conservative resolution: if this bar touched both the stop and a target,
    # the stop wins. We cannot prove ordering from OHLC, so we take the loss.
    if stop_hit:
        if hits:
            close_reason = (
                "Stop and target were both inside this bar's range. Ordering cannot be "
                "established from OHLC, so this is recorded as a stop-out."
            )
        else:
            close_reason = "Stop loss reached."
        events.append(("STOPPED", pos.stop_loss, close_reason))
        status = "STOPPED"
        close_price = pos.stop_loss
        outcome = "WIN" if tp_hits >= 1 else "LOSS"
        if tp_hits >= 1:
            close_reason += " Earlier targets had already been reached."
    else:
        for n in hits:
            if n <= tp_hits:
                continue
            level = dict(targets)[n]
            events.append((f"TP{n}_HIT", level, f"Take profit {n} reached."))
            tp_hits = n
            status = f"TP{n}_HIT"
        if tp_hits == 3:
            outcome = "WIN"
            close_price = pos.tp3
            close_reason = "All three targets reached."

    if outcome is None and bar.ts >= pos.expires_at:
        events.append(("EXPIRED", bar.close, "Signal validity window elapsed."))
        status = "EXPIRED"
        close_price = bar.close
        close_reason = "Signal validity window elapsed."
        outcome = "WIN" if tp_hits >= 1 else "EXPIRED"

    pnl_pct: float | None = None
    r_multiple: float | None = None
    if close_price is not None:
        pnl_pct = _excursion(pos.direction, pos.entry, close_price) * 100.0
        risk = abs(pos.entry - pos.stop_loss)
        if risk > 0:
            r_multiple = (close_price - pos.entry) / risk
            if pos.direction == "SHORT":
                r_multiple = -r_multiple

    return LifecycleUpdate(
        events=events,
        status=status,
        tp_hits=tp_hits,
        mfe_pct=mfe * 100.0,
        mae_pct=mae * 100.0,
        outcome=outcome,
        close_price=close_price,
        close_reason=close_reason,
        pnl_pct=pnl_pct,
        r_multiple=r_multiple,
    )


async def load_open_positions(session: AsyncSession, symbol: str | None = None) -> list[SignalPosition]:
    rows = await session.execute(
        text(
            """
            SELECT s.signal_id, s.direction, s.entry, s.stop_loss, s.tp1, s.tp2, s.tp3,
                   s.expires_at, st.status, st.tp_hits, st.mfe_pct, st.mae_pct, s.symbol
            FROM signals s
            JOIN signal_state st ON st.signal_id = s.signal_id
            WHERE st.outcome IS NULL
              AND (:symbol::text IS NULL OR s.symbol = :symbol)
            ORDER BY s.published_at
            """
        ),
        {"symbol": symbol},
    )
    return [
        SignalPosition(
            signal_id=str(r.signal_id),
            direction=r.direction,
            entry=float(r.entry),
            stop_loss=float(r.stop_loss),
            tp1=float(r.tp1),
            tp2=float(r.tp2),
            tp3=float(r.tp3),
            expires_at=r.expires_at,
            status=r.status,
            tp_hits=int(r.tp_hits),
            mfe_pct=float(r.mfe_pct) / 100.0,
            mae_pct=float(r.mae_pct) / 100.0,
        )
        for r in rows
    ]


async def apply_update(session: AsyncSession, pos: SignalPosition, upd: LifecycleUpdate, bar: Bar) -> None:
    for event, price, reason in upd.events:
        await session.execute(
            text(
                """
                INSERT INTO signal_events (signal_id, event, ts, price, reason)
                VALUES (:sid, :event, :ts, :price, :reason)
                """
            ),
            {"sid": pos.signal_id, "event": event, "ts": bar.ts, "price": price, "reason": reason},
        )

    await session.execute(
        text(
            """
            UPDATE signal_state
               SET status = :status,
                   tp_hits = :tp_hits,
                   mfe_pct = :mfe,
                   mae_pct = :mae,
                   outcome = COALESCE(:outcome, outcome),
                   close_price = COALESCE(:close_price, close_price),
                   closed_at = CASE WHEN :outcome IS NOT NULL THEN :ts ELSE closed_at END,
                   close_reason = COALESCE(:close_reason, close_reason),
                   pnl_pct = COALESCE(:pnl, pnl_pct),
                   r_multiple = COALESCE(:r_mult, r_multiple),
                   updated_at = now()
             WHERE signal_id = :sid
            """
        ),
        {
            "sid": pos.signal_id,
            "status": upd.status,
            "tp_hits": upd.tp_hits,
            "mfe": upd.mfe_pct,
            "mae": upd.mae_pct,
            "outcome": upd.outcome,
            "close_price": upd.close_price,
            "close_reason": upd.close_reason,
            "pnl": upd.pnl_pct,
            "r_mult": upd.r_multiple,
            "ts": bar.ts,
        },
    )


async def cancel(session: AsyncSession, signal_id: str, reason: str) -> None:
    """Cancel a signal. The row stays; the reason is recorded permanently."""
    now = datetime.now(timezone.utc)
    await session.execute(
        text(
            """
            INSERT INTO signal_events (signal_id, event, ts, reason)
            VALUES (:sid, 'CANCELLED', :ts, :reason)
            """
        ),
        {"sid": signal_id, "ts": now, "reason": reason},
    )
    await session.execute(
        text(
            """
            UPDATE signal_state
               SET status = 'CANCELLED', outcome = 'CANCELLED', closed_at = :ts,
                   close_reason = :reason, updated_at = now()
             WHERE signal_id = :sid
            """
        ),
        {"sid": signal_id, "ts": now, "reason": reason},
    )


async def rebuild_state_from_events(session: AsyncSession, signal_id: str) -> None:
    """Recompute signal_state purely from the append-only event log.

    Proof that the cache is disposable: if it is ever wrong, this makes it right
    again without touching a single immutable row.
    """
    rows = await session.execute(
        text("SELECT event, ts, price, reason FROM signal_events WHERE signal_id = :sid ORDER BY id"),
        {"sid": signal_id},
    )
    status = "ACTIVE"
    tp_hits = 0
    outcome = None
    close_price = None
    close_reason = None
    closed_at = None
    for r in rows:
        status = r.event
        if r.event.startswith("TP") and r.event.endswith("_HIT"):
            tp_hits = max(tp_hits, int(r.event[2]))
        if r.event in TERMINAL:
            closed_at = r.ts
            close_price = float(r.price) if r.price is not None else None
            close_reason = r.reason
            if r.event == "STOPPED":
                outcome = "WIN" if tp_hits >= 1 else "LOSS"
            elif r.event == "TP3_HIT":
                outcome = "WIN"
            elif r.event == "EXPIRED":
                outcome = "WIN" if tp_hits >= 1 else "EXPIRED"
            else:
                outcome = "CANCELLED"

    await session.execute(
        text(
            """
            UPDATE signal_state
               SET status = :status, tp_hits = :tp_hits, outcome = :outcome,
                   close_price = :close_price, close_reason = :close_reason,
                   closed_at = :closed_at, updated_at = now()
             WHERE signal_id = :sid
            """
        ),
        {
            "sid": signal_id,
            "status": status,
            "tp_hits": tp_hits,
            "outcome": outcome,
            "close_price": close_price,
            "close_reason": close_reason,
            "closed_at": closed_at,
        },
    )
