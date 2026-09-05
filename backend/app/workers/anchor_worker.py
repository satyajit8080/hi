"""Daily Merkle anchoring.

Every day the previous day's signal hashes are hashed into a Merkle tree and the
root is stored. With `SP_ANCHOR_PROVIDER=opentimestamps` the root is also
submitted to an OpenTimestamps calendar, which eventually commits it into a
Bitcoin block — that is what makes "this signal existed before the outcome was
known" checkable by someone who does not trust us.

The local provider stores the root without external attestation. It still
detects tampering; it just cannot prove *when* on its own.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from app.config import settings
from app.core.hashchain import merkle_root
from app.db import SessionLocal, close_connections

log = logging.getLogger("signalproof.anchor")
CYCLE_SECONDS = 3600


async def anchor_day(session, day: date) -> dict | None:
    rows = await session.execute(
        text(
            """
            SELECT seq, row_hash
            FROM signals
            WHERE published_at >= :start AND published_at < :end
            ORDER BY seq
            """
        ),
        {
            "start": datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
            "end": datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc),
        },
    )
    records = list(rows)
    if not records:
        return None

    leaves = [r.row_hash for r in records]
    root = merkle_root(leaves)
    head = await session.execute(text("SELECT row_hash FROM signals ORDER BY seq DESC LIMIT 1"))

    proof_status = "local"
    if settings.anchor_provider == "opentimestamps":
        proof_status = await _submit_ots(root)

    await session.execute(
        text(
            """
            INSERT INTO anchors (day, merkle_root, signal_count, first_seq, last_seq,
                                 chain_head, provider, proof_status)
            VALUES (:day, :root, :count, :first, :last, :head, :provider, :status)
            ON CONFLICT (day) DO NOTHING
            """
        ),
        {
            "day": day,
            "root": root,
            "count": len(leaves),
            "first": records[0].seq,
            "last": records[-1].seq,
            "head": head.scalar() or "",
            "provider": settings.anchor_provider,
            "status": proof_status,
        },
    )
    await session.commit()
    return {"day": day.isoformat(), "merkle_root": root, "signals": len(leaves)}


async def _submit_ots(root: str) -> str:
    """Submit the root digest to an OpenTimestamps calendar."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://alice.btc.calendar.opentimestamps.org/digest",
                content=bytes.fromhex(root),
                headers={"Content-Type": "application/octet-stream"},
            )
            return "submitted" if r.status_code == 200 else f"failed_{r.status_code}"
    except httpx.HTTPError as exc:
        log.warning("OpenTimestamps submission failed: %s", exc)
        return "failed_network"


async def main() -> None:
    logging.basicConfig(level=settings.log_level.upper())
    log.info("anchor worker starting — provider=%s", settings.anchor_provider)
    try:
        while True:
            try:
                async with SessionLocal() as session:
                    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
                    result = await anchor_day(session, yesterday)
                    if result:
                        log.info("anchored %s", result)
            except Exception:  # noqa: BLE001
                log.exception("anchor cycle failed")
            await asyncio.sleep(CYCLE_SECONDS)
    finally:
        await close_connections()


if __name__ == "__main__":
    asyncio.run(main())
