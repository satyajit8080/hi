"""Watchlist state machine — what the engine saw and why it did not trade.

    WATCHING       directional evidence exists but the class is NO_TRADE
    SETUP_FORMING  class is tradeable but a gate or the risk engine blocked it
    PROMOTED       published as a signal (linked)
    DROPPED        evidence faded

Every transition lands in `candidate_events`, which is append-only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

WATCH_FLOOR = 0.15   # |raw score| needed before something counts as "watching"


async def _open(session: AsyncSession, symbol: str, timeframe: str):
    row = await session.execute(
        text("""SELECT id, state FROM candidates
                WHERE symbol = :s AND timeframe = :tf AND state IN ('WATCHING','SETUP_FORMING')"""),
        {"s": symbol, "tf": timeframe},
    )
    return row.first()


async def _event(session, cid: int, frm: str | None, to: str, cls: str, strength: int, reasons: list) -> None:
    await session.execute(
        text("""INSERT INTO candidate_events (candidate_id, from_state, to_state, signal_class, strength, reasons)
                VALUES (:cid, :frm, :to, :cls, :strength, CAST(:reasons AS jsonb))"""),
        {"cid": cid, "frm": frm, "to": to, "cls": cls, "strength": strength, "reasons": json.dumps(reasons)},
    )


async def observe(
    session: AsyncSession, symbol: str, timeframe: str, direction: str, signal_class: str,
    strength: int, raw_score: float, conflict: float, blockers: list[str],
    signal_id: str | None, is_simulated: bool,
) -> str | None:
    """Reconcile one (symbol, timeframe) observation with the watchlist. Returns new state."""
    existing = await _open(session, symbol, timeframe)

    if signal_id is not None:
        target = "PROMOTED"
    elif signal_class != "NO_TRADE" and blockers:
        target = "SETUP_FORMING"
    elif abs(raw_score) >= WATCH_FLOOR and direction != "NONE":
        target = "WATCHING"
    else:
        target = None

    if existing is None:
        if target is None or target == "PROMOTED":
            return None
        row = await session.execute(
            text("""INSERT INTO candidates (symbol, timeframe, direction, state, signal_class, strength,
                                            conflict, blockers, is_simulated)
                    VALUES (:s, :tf, :d, :st, :cls, :str, :conf, CAST(:bl AS jsonb), :sim) RETURNING id"""),
            {"s": symbol, "tf": timeframe, "d": direction, "st": target, "cls": signal_class,
             "str": strength, "conf": conflict, "bl": json.dumps(blockers), "sim": is_simulated},
        )
        cid = int(row.scalar_one())
        await _event(session, cid, None, target, signal_class, strength, blockers)
        return target

    cid, state = int(existing.id), existing.state
    new_state = target or "DROPPED"
    await session.execute(
        text("""UPDATE candidates SET state = :st, direction = :d, signal_class = :cls, strength = :str,
                    conflict = :conf, blockers = CAST(:bl AS jsonb), signal_id = COALESCE(:sid, signal_id),
                    last_seen = now()
                WHERE id = :cid"""),
        {"st": new_state, "d": direction, "cls": signal_class, "str": strength, "conf": conflict,
         "bl": json.dumps(blockers), "sid": signal_id, "cid": cid},
    )
    if new_state != state:
        await _event(session, cid, state, new_state, signal_class, strength, blockers)
    return new_state


async def open_candidates(session: AsyncSession, limit: int = 30) -> list[dict]:
    rows = await session.execute(
        text("""SELECT id, symbol, timeframe, direction, state, signal_class, strength, conflict,
                       blockers, first_seen, last_seen, is_simulated
                FROM candidates WHERE state IN ('WATCHING','SETUP_FORMING')
                ORDER BY last_seen DESC LIMIT :limit"""),
        {"limit": limit},
    )
    out = []
    for r in rows:
        d = dict(r._mapping)
        d["first_seen"] = d["first_seen"].isoformat()
        d["last_seen"] = d["last_seen"].isoformat()
        d["conflict"] = float(d["conflict"])
        out.append(d)
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
