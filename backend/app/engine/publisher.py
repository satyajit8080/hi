"""Signal validation and publication.

Publication is the point of no return. Once a row lands in `signals` it cannot
be edited or deleted — not by the app, not by a migration run by mistake. So
everything that can be checked is checked here, before the write.

The write itself is serialised through a Postgres advisory lock so two engine
workers cannot both read the same chain head and fork the ledger.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.hashchain import GENESIS, compute_row_hash
from app.engine.calibration import CalibrationResult
from app.engine.risk import RiskPlan
from app.engine.scoring import ScoreResult, driver_contribution_total
from app.features.snapshot import FeatureSnapshot

CHAIN_LOCK_KEY = 8_675_309  # arbitrary but stable advisory-lock id


class ValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class PublishedSignal:
    signal_id: str
    row_hash: str
    prev_hash: str
    seq: int
    published_at: datetime


def validate(snapshot: FeatureSnapshot, score: ScoreResult, plan: RiskPlan) -> None:
    """Structural sanity. Anything failing here is an engine bug, not a market."""
    if score.direction not in ("LONG", "SHORT"):
        raise ValidationError("NO_DIRECTION", "Signal has no direction.")

    if score.direction == "LONG":
        if not plan.stop_loss < plan.entry:
            raise ValidationError("BAD_STOP", "A long stop must sit below entry.")
        if not plan.entry < plan.tp1 < plan.tp2 < plan.tp3:
            raise ValidationError("BAD_TARGETS", "Long targets must ascend above entry.")
    else:
        if not plan.stop_loss > plan.entry:
            raise ValidationError("BAD_STOP", "A short stop must sit above entry.")
        if not plan.entry > plan.tp1 > plan.tp2 > plan.tp3:
            raise ValidationError("BAD_TARGETS", "Short targets must descend below entry.")

    if plan.risk_reward < settings.min_rr:
        raise ValidationError("RR_TOO_LOW", f"R:R {plan.risk_reward} below floor {settings.min_rr}.")
    if plan.expires_at <= datetime.now(timezone.utc):
        raise ValidationError("ALREADY_EXPIRED", "Expiry is in the past.")
    if not 0 <= score.strength <= 100:
        raise ValidationError("BAD_STRENGTH", "Strength outside 0-100.")

    # The core transparency invariant: only promoted features may carry weight.
    for r in score.reason_codes:
        if r.get("role") == "context_only" and abs(r.get("contribution", 0.0)) > 1e-9:
            raise ValidationError(
                "CONTEXT_LEAKED_INTO_SCORE",
                f"Context-only reason {r['code']} carries a non-zero contribution.",
            )
    if not snapshot.context.is_signal_driver:
        for r in score.reason_codes:
            if r.get("group") == "context_onchain" and abs(r.get("contribution", 0.0)) > 1e-9:
                raise ValidationError(
                    "UNVALIDATED_ONCHAIN_DRIVER",
                    "On-chain features scored without a passing validation record.",
                )


async def _duplicate_exists(
    session: AsyncSession, symbol: str, timeframe: str, direction: str, setup_hash: str
) -> bool:
    """Suppress a re-publish of the same setup while the previous one is live."""
    row = await session.execute(
        text(
            """
            SELECT 1
            FROM signals s
            JOIN signal_state st ON st.signal_id = s.signal_id
            WHERE s.symbol = :symbol
              AND s.timeframe = :timeframe
              AND s.direction = :direction
              AND s.features->'setup_hash' = to_jsonb(:setup_hash::text)
              AND st.outcome IS NULL
            LIMIT 1
            """
        ),
        {"symbol": symbol, "timeframe": timeframe, "direction": direction, "setup_hash": setup_hash},
    )
    return row.first() is not None


async def publish(
    session: AsyncSession,
    snapshot: FeatureSnapshot,
    score: ScoreResult,
    plan: RiskPlan,
    calibration: CalibrationResult,
    extra_features: dict[str, Any] | None = None,
) -> PublishedSignal | None:
    validate(snapshot, score, plan)
    if score.signal_class == "NO_TRADE":
        raise ValidationError("NO_TRADE_PUBLISHED", "A NO_TRADE classification can never be published.")

    features = snapshot.to_dict()
    features["signal_class"] = score.signal_class
    features["conflict"] = round(float(score.conflict), 4)
    if extra_features:
        features.update(extra_features)
    features["component_scores"] = {k: round(v, 6) for k, v in score.component_scores.items()}
    features["driver_contribution_total"] = round(driver_contribution_total(score.reason_codes), 6)
    features["stop_method"] = plan.stop_method
    features["engine_notes"] = score.notes + plan.notes
    features["setup_hash"] = _setup_hash(snapshot, score)

    if await _duplicate_exists(session, snapshot.symbol, snapshot.timeframe, score.direction,
                               features["setup_hash"]):
        return None

    # Serialise chain-head reads so concurrent workers cannot fork the ledger.
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CHAIN_LOCK_KEY})
    head = await session.execute(
        text("SELECT row_hash FROM signals ORDER BY seq DESC LIMIT 1")
    )
    prev_hash = head.scalar() or GENESIS

    signal_id = str(uuid.uuid4())
    published_at = datetime.now(timezone.utc)

    payload: dict[str, Any] = {
        "signal_id": signal_id,
        "published_at": published_at,
        "symbol": snapshot.symbol,
        "direction": score.direction,
        "timeframe": snapshot.timeframe,
        "regime": snapshot.regime.regime,
        "strategy_version": settings.strategy_version,
        "strength": score.strength,
        "mtf_alignment": round(score.mtf_alignment, 4),
        "entry": plan.entry,
        "stop_loss": plan.stop_loss,
        "tp1": plan.tp1,
        "tp2": plan.tp2,
        "tp3": plan.tp3,
        "risk_reward": plan.risk_reward,
        "expires_at": plan.expires_at,
        "invalidation": plan.invalidation,
        "features": features,
        "reason_codes": score.reason_codes,
        "risk_flags": score.risk_flags,
        "data_quality": snapshot.quality.to_dict(),
        "is_simulated": snapshot.is_simulated,
    }
    row_hash = compute_row_hash(payload, prev_hash)

    result = await session.execute(
        text(
            """
            INSERT INTO signals (
                signal_id, published_at, symbol, direction, timeframe, regime,
                strategy_version, strength, mtf_alignment, calibrated_winrate,
                calibration_sample, calibration_ci_low, calibration_ci_high,
                entry, stop_loss, tp1, tp2, tp3, risk_reward, risk_category,
                expires_at, invalidation, features, reason_codes, risk_flags,
                data_quality, is_simulated, prev_hash, row_hash
            ) VALUES (
                :signal_id, :published_at, :symbol, :direction, :timeframe, :regime,
                :strategy_version, :strength, :mtf_alignment, :calibrated_winrate,
                :calibration_sample, :ci_low, :ci_high,
                :entry, :stop_loss, :tp1, :tp2, :tp3, :risk_reward, :risk_category,
                :expires_at, :invalidation, CAST(:features AS jsonb),
                CAST(:reason_codes AS jsonb), CAST(:risk_flags AS jsonb),
                CAST(:data_quality AS jsonb), :is_simulated, :prev_hash, :row_hash
            )
            RETURNING seq
            """
        ),
        {
            **{k: v for k, v in payload.items() if k not in
               ("features", "reason_codes", "risk_flags", "data_quality")},
            "features": _json(features),
            "reason_codes": _json(score.reason_codes),
            "risk_flags": _json(score.risk_flags),
            "data_quality": _json(snapshot.quality.to_dict()),
            "calibrated_winrate": calibration.win_rate,
            "calibration_sample": calibration.sample_size,
            "ci_low": calibration.ci_low,
            "ci_high": calibration.ci_high,
            "risk_category": plan.risk_category,
            "prev_hash": prev_hash,
            "row_hash": row_hash,
        },
    )
    seq = int(result.scalar_one())

    await session.execute(
        text(
            """
            INSERT INTO signal_events (signal_id, event, ts, price, reason, meta)
            VALUES (:sid, 'SIGNAL_GENERATED', :ts, :price, :reason, CAST(:meta AS jsonb)),
                   (:sid, 'ACTIVE', :ts, :price, 'Signal published and now tracking', '{}'::jsonb)
            """
        ),
        {
            "sid": signal_id,
            "ts": published_at,
            "price": plan.entry,
            "reason": f"{score.direction} {snapshot.symbol} on {snapshot.timeframe}",
            "meta": _json({"strength": score.strength, "regime": snapshot.regime.regime}),
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO signal_state (signal_id, status, mfe_pct, mae_pct)
            VALUES (:sid, 'ACTIVE', 0, 0)
            """
        ),
        {"sid": signal_id},
    )

    return PublishedSignal(signal_id, row_hash, prev_hash, seq, published_at)


def _setup_hash(snapshot: FeatureSnapshot, score: ScoreResult) -> str:
    """Identity of a setup, for duplicate suppression across candles."""
    from app.core.hashchain import sha256_hex

    key = "|".join(
        [
            snapshot.symbol,
            snapshot.timeframe,
            score.direction,
            snapshot.regime.regime,
            ",".join(sorted(r["code"] for r in score.reason_codes if r.get("role") == "driver")),
        ]
    )
    return sha256_hex(key)[:32]


def _json(value: Any) -> str:
    import json

    def default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, "to_dict"):
            return o.to_dict()
        return str(o)

    return json.dumps(value, default=default, separators=(",", ":"))
