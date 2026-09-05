"""Apply SQL migrations in order, then register the running strategy version.

Idempotent: every migration is written so re-running it is safe, and applied
files are recorded in `schema_migrations`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal, engine

log = logging.getLogger("signalproof.migrate")
MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"


async def run() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename    text PRIMARY KEY,
                    checksum    text NOT NULL,
                    applied_at  timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        )

    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text()
        checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]
        async with engine.begin() as conn:
            existing = await conn.execute(
                text("SELECT checksum FROM schema_migrations WHERE filename = :f"),
                {"f": path.name},
            )
            row = existing.first()
            if row is not None:
                if row.checksum != checksum:
                    log.warning(
                        "%s changed since it was applied. Write a new migration instead of "
                        "editing an applied one.", path.name
                    )
                continue
            log.info("applying %s", path.name)
            await conn.execute(text(sql))
            await conn.execute(
                text("INSERT INTO schema_migrations (filename, checksum) VALUES (:f, :c)"),
                {"f": path.name, "c": checksum},
            )

    await _register_version()
    log.info("migrations complete")


async def _register_version() -> None:
    from app.engine.scoring import MAX_CONFLICT, STRONG_THRESHOLD, WEAK_THRESHOLD, MICRO_WEIGHT_BY_TF, STRATEGY_WEIGHTS
    from app.engine.strategies import REGIME_GATES

    config = {
        "weights": STRATEGY_WEIGHTS,
        "micro_weight_by_tf": MICRO_WEIGHT_BY_TF,
        "regime_gates": REGIME_GATES,
        "min_rr": settings.min_rr,
        # 1.1.0: classification thresholds and the conflict ceiling are part of
        # the strategy, so they are part of the recorded config hash.
        "strong_threshold": STRONG_THRESHOLD,
        "weak_threshold": WEAK_THRESHOLD,
        "max_conflict": MAX_CONFLICT,
    }
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO strategy_versions (version, notes, weights, config_hash)
                VALUES (:v, :notes, CAST(:weights AS jsonb), :hash)
                ON CONFLICT (version) DO UPDATE
                    SET weights = EXCLUDED.weights, config_hash = EXCLUDED.config_hash
                """
            ),
            {
                "v": settings.strategy_version,
                "notes": ("1.1.0: five-state classification with NO TRADE on contradiction (conflict >= 0.40), "
                          "counter-trend demoted to watch, closed candles only, unavailable data blocks "
                          "excluded from renormalisation, spoof-discounted order flow, OI/funding-z/liquidation "
                          "derivatives. Fixed expert weights; order flow limited to 5m/15m."),
                "weights": json.dumps(config),
                "hash": config_hash,
            },
        )
        await session.commit()


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level.upper())
    asyncio.run(run())
