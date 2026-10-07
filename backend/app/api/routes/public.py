"""Public API.

Everything needed to audit the track record is here and needs no account:
signal history, outcomes, per-signal hash verification, chain verification and
a full CSV export. A proof behind a paywall is not a proof.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, optional_user, tier_of
from app.config import PUBLISH_TFS, settings
from app.core.hashchain import compute_row_hash, merkle_proof, merkle_root, verify_chain
from app.db import get_redis, session_dep
from app.services import candidates as candidate_svc
from app.services import explain as explain_svc
from app.services import performance as perf
from app.services import smartmoney
from app.services.subscriptions import (
    allowed_symbols,
    history_floor,
    redact_for_tier,
    tier_summary,
)

router = APIRouter(prefix="/v1", tags=["public"])

SIGNAL_COLUMNS = """
    s.signal_id, s.published_at, s.symbol, s.direction, s.timeframe, s.regime,
    s.strategy_version, s.strength, s.mtf_alignment, s.calibrated_winrate,
    s.calibration_sample, s.calibration_ci_low, s.calibration_ci_high,
    s.entry, s.stop_loss, s.tp1, s.tp2, s.tp3, s.risk_reward, s.risk_category,
    s.expires_at, s.invalidation, s.reason_codes, s.risk_flags, s.data_quality,
    s.is_simulated, s.row_hash, s.prev_hash, s.seq,
    s.features->>'signal_class' AS signal_class,
    st.status, st.outcome, st.tp_hits, st.mfe_pct, st.mae_pct,
    st.close_price, st.closed_at, st.close_reason, st.pnl_pct, st.r_multiple
"""


def _row_to_signal(row) -> dict:
    d = dict(row._mapping)
    d["signal_id"] = str(d["signal_id"])
    for k in ("entry", "stop_loss", "tp1", "tp2", "tp3", "risk_reward", "mtf_alignment",
              "calibrated_winrate", "calibration_ci_low", "calibration_ci_high",
              "mfe_pct", "mae_pct", "close_price", "pnl_pct", "r_multiple"):
        if d.get(k) is not None:
            d[k] = float(d[k])
    return d


@router.get("/signals/live")
async def live_signals(
    symbol: str | None = None,
    user: CurrentUser | None = Depends(optional_user),
    session: AsyncSession = Depends(session_dep),
):
    tier = tier_of(user)
    symbols = allowed_symbols(tier)
    rows = await session.execute(
        text(
            f"""
            SELECT {SIGNAL_COLUMNS}
            FROM signals s JOIN signal_state st ON st.signal_id = s.signal_id
            WHERE st.outcome IS NULL
              AND (CAST(:symbol AS text) IS NULL OR s.symbol = :symbol)
              AND (:all_symbols OR s.symbol = ANY(:symbols))
            ORDER BY s.published_at DESC
            LIMIT 60
            """
        ),
        {
            "symbol": symbol,
            "all_symbols": symbols is None,
            "symbols": list(symbols or settings.symbols),
        },
    )
    signals = [redact_for_tier(_row_to_signal(r), tier) for r in rows]
    return {
        "signals": signals,
        "tier": tier_summary(tier),
        "is_simulated": settings.is_replay,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/signals/history")
async def signal_history(
    symbol: str | None = None,
    timeframe: str | None = None,
    outcome: str | None = None,
    direction: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    user: CurrentUser | None = Depends(optional_user),
    session: AsyncSession = Depends(session_dep),
):
    """Full history, including every loser. Nothing is filtered by outcome
    unless the caller asks for it."""
    tier = tier_of(user)
    floor = history_floor(tier)
    rows = await session.execute(
        text(
            f"""
            SELECT {SIGNAL_COLUMNS}
            FROM signals s JOIN signal_state st ON st.signal_id = s.signal_id
            WHERE (CAST(:symbol AS text) IS NULL OR s.symbol = :symbol)
              AND (CAST(:timeframe AS text) IS NULL OR s.timeframe = :timeframe)
              AND (CAST(:outcome AS text) IS NULL OR st.outcome = :outcome)
              AND (CAST(:direction AS text) IS NULL OR s.direction = :direction)
              AND (CAST(:floor AS timestamptz) IS NULL OR s.published_at >= :floor)
            ORDER BY s.published_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "symbol": symbol, "timeframe": timeframe, "outcome": outcome,
            "direction": direction, "floor": floor, "limit": limit, "offset": offset,
        },
    )
    total = await session.execute(text("SELECT count(*) FROM signals"))
    return {
        "signals": [redact_for_tier(_row_to_signal(r), tier) for r in rows],
        "total_published": int(total.scalar_one()),
        "limit": limit,
        "offset": offset,
        "history_limited": floor is not None,
        "tier": tier_summary(tier),
    }


@router.get("/signals/{signal_id}")
async def signal_detail(
    signal_id: str,
    user: CurrentUser | None = Depends(optional_user),
    session: AsyncSession = Depends(session_dep),
):
    row = await session.execute(
        text(
            f"""
            SELECT {SIGNAL_COLUMNS}, s.features
            FROM signals s JOIN signal_state st ON st.signal_id = s.signal_id
            WHERE s.signal_id = :sid
            """
        ),
        {"sid": signal_id},
    )
    r = row.first()
    if r is None:
        raise HTTPException(404, "No signal with that id.")
    signal = redact_for_tier(_row_to_signal(r), tier_of(user))

    events = await session.execute(
        text(
            """
            SELECT event, ts, price, reason, meta
            FROM signal_events WHERE signal_id = :sid ORDER BY id
            """
        ),
        {"sid": signal_id},
    )
    signal["lifecycle"] = [
        {
            "event": e.event,
            "ts": e.ts.isoformat(),
            "price": float(e.price) if e.price is not None else None,
            "reason": e.reason,
            "meta": e.meta,
        }
        for e in events
    ]
    signal["signal_class"] = (signal.get("features") or {}).get("signal_class")
    signal["conflict"] = (signal.get("features") or {}).get("conflict")
    signal["verification"] = {
        "row_hash": signal["row_hash"],
        "prev_hash": signal["prev_hash"],
        "verify_url": f"/v1/verify/{signal_id}",
        "method": "sha256(canonical_payload || prev_hash)",
    }
    return signal


@router.get("/verify/{signal_id}")
async def verify_signal(signal_id: str, session: AsyncSession = Depends(session_dep)):
    """Recompute one signal's hash from its stored payload.

    Returns the canonical payload too, so anyone can run the same SHA-256
    themselves without trusting this endpoint's answer.
    """
    row = await session.execute(
        text(
            """
            SELECT signal_id, published_at, symbol, direction, timeframe, regime,
                   strategy_version, strength, mtf_alignment, entry, stop_loss,
                   tp1, tp2, tp3, risk_reward, expires_at, invalidation, features,
                   reason_codes, risk_flags, data_quality, is_simulated,
                   prev_hash, row_hash, seq
            FROM signals WHERE signal_id = :sid
            """
        ),
        {"sid": signal_id},
    )
    r = row.first()
    if r is None:
        raise HTTPException(404, "No signal with that id.")

    payload = dict(r._mapping)
    payload["signal_id"] = str(payload["signal_id"])
    for k in ("entry", "stop_loss", "tp1", "tp2", "tp3", "risk_reward", "mtf_alignment"):
        payload[k] = float(payload[k])
    recomputed = compute_row_hash(payload, payload["prev_hash"])

    from app.core.hashchain import canonical_payload

    return {
        "signal_id": payload["signal_id"],
        "seq": payload["seq"],
        "stored_hash": payload["row_hash"],
        "recomputed_hash": recomputed,
        "valid": recomputed == payload["row_hash"],
        "prev_hash": payload["prev_hash"],
        "canonical_payload": canonical_payload(payload),
        "how_to_verify": (
            "sha256(canonical_payload + prev_hash) must equal stored_hash. "
            "The canonical payload is sorted-key JSON with compact separators and all "
            "numbers rendered to 8 decimal places."
        ),
    }


@router.get("/verify/chain/full")
async def verify_full_chain(
    limit: int = Query(5000, le=50000), session: AsyncSession = Depends(session_dep)
):
    """Walk the whole ledger and report the first break, if there is one."""
    rows = await session.execute(
        text(
            """
            SELECT signal_id, published_at, symbol, direction, timeframe, regime,
                   strategy_version, strength, mtf_alignment, entry, stop_loss,
                   tp1, tp2, tp3, risk_reward, expires_at, invalidation, features,
                   reason_codes, risk_flags, data_quality, is_simulated,
                   prev_hash, row_hash, seq
            FROM signals ORDER BY seq LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    payloads = []
    for r in rows:
        d = dict(r._mapping)
        d["signal_id"] = str(d["signal_id"])
        for k in ("entry", "stop_loss", "tp1", "tp2", "tp3", "risk_reward", "mtf_alignment"):
            d[k] = float(d[k])
        payloads.append(d)
    result = verify_chain(payloads)
    result["signals_in_chain"] = len(payloads)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    return result


@router.get("/anchors")
async def anchors(session: AsyncSession = Depends(session_dep)):
    rows = await session.execute(
        text(
            """
            SELECT day, merkle_root, signal_count, first_seq, last_seq, chain_head,
                   provider, proof_status, anchored_at
            FROM anchors ORDER BY day DESC LIMIT 400
            """
        )
    )
    return {
        "anchors": [
            {**dict(r._mapping), "day": r.day.isoformat(), "anchored_at": r.anchored_at.isoformat()}
            for r in rows
        ],
        "provider": settings.anchor_provider,
        "explanation": (
            "Each day's signal hashes are hashed into a Merkle tree and the root is published. "
            "With OpenTimestamps enabled the root is also committed to Bitcoin, which pins the "
            "ledger to a point in time we do not control."
        ),
    }


@router.get("/anchors/{day}/proof/{signal_id}")
async def anchor_proof(day: str, signal_id: str, session: AsyncSession = Depends(session_dep)):
    try:
        day_value = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Day must be an ISO date (YYYY-MM-DD).")
    anchor = await session.execute(
        text("SELECT merkle_root, first_seq, last_seq FROM anchors WHERE day = :day"), {"day": day_value}
    )
    a = anchor.first()
    if a is None:
        raise HTTPException(404, "No anchor for that day.")
    rows = await session.execute(
        text("SELECT signal_id, row_hash FROM signals WHERE seq BETWEEN :a AND :b ORDER BY seq"),
        {"a": a.first_seq, "b": a.last_seq},
    )
    leaves = [r.row_hash for r in rows]
    ids = [str(r.signal_id) for r in rows]
    if signal_id not in ids:
        raise HTTPException(404, "That signal is not in this day's anchor.")
    index = ids.index(signal_id)
    return {
        "signal_id": signal_id,
        "day": day,
        "leaf": leaves[index],
        "merkle_root": a.merkle_root,
        "computed_root": merkle_root(leaves),
        "proof": merkle_proof(leaves, index),
    }


@router.get("/export/signals.csv")
async def export_csv(
    symbol: str | None = None,
    since: str | None = None,
    session: AsyncSession = Depends(session_dep),
):
    """Complete signal log as CSV. Deliberately unauthenticated."""
    since_value: datetime | None = None
    if since:
        try:
            since_value = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(400, "since must be an ISO 8601 date or timestamp.")
        if since_value.tzinfo is None:
            since_value = since_value.replace(tzinfo=timezone.utc)
    rows = await session.execute(
        text(
            """
            SELECT s.signal_id, s.published_at, s.symbol, s.direction, s.timeframe,
                   s.regime, s.strategy_version, s.strength, s.calibrated_winrate,
                   s.entry, s.stop_loss, s.tp1, s.tp2, s.tp3, s.risk_reward,
                   s.expires_at, st.status, st.outcome, st.tp_hits, st.mfe_pct,
                   st.mae_pct, st.close_price, st.closed_at, st.close_reason,
                   st.pnl_pct, st.r_multiple, s.is_simulated, s.row_hash, s.prev_hash
            FROM signals s LEFT JOIN signal_state st ON st.signal_id = s.signal_id
            WHERE (CAST(:symbol AS text) IS NULL OR s.symbol = :symbol)
              AND (CAST(:since AS timestamptz) IS NULL OR s.published_at >= :since)
            ORDER BY s.seq
            """
        ),
        {"symbol": symbol, "since": since_value},
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    first = True
    for r in rows:
        d = dict(r._mapping)
        if first:
            writer.writerow(d.keys())
            first = False
        writer.writerow(["" if v is None else v for v in d.values()])
    if first:
        writer.writerow(["signal_id", "published_at", "symbol"])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="signalproof_signals.csv"'},
    )


@router.get("/performance")
async def performance(
    window: str = Query("all", pattern="^(30d|90d|6m|1y|all)$"),
    session: AsyncSession = Depends(session_dep),
):
    return await perf.performance_report(session, window)


@router.get("/performance/calibration")
async def calibration(session: AsyncSession = Depends(session_dep)):
    report = await perf.performance_report(session, "all")
    return {
        "curve": report["calibration_curve"],
        "sample": report["calibration_sample"],
        "minimum_sample": settings.min_calibration_sample,
        "explanation": (
            "Each point compares the win rate we predicted against the win rate that actually "
            "happened. A well-calibrated engine sits on the diagonal. Bins with too few closed "
            "signals are shown empty rather than filled with a number we cannot support."
        ),
    }


@router.get("/market/{symbol}")
async def market(symbol: str):
    redis = get_redis()
    raw = await redis.get(f"sp:market:{symbol.upper()}")
    if raw is None:
        raise HTTPException(503, "Market data is not available yet. The ingest worker may be starting.")
    return json.loads(raw)


@router.get("/status")
async def status():
    redis = get_redis()
    raw = await redis.get("sp:health")
    health = json.loads(raw) if raw else {
        "degraded": True, "venues_live": 0, "flags": ["ingest_worker_not_reporting"],
        "max_staleness_ms": None, "venues": {},
    }
    stats_raw = await redis.get("sp:engine:stats")
    engine = json.loads(stats_raw) if stats_raw else {"status": "not_reporting"}
    return {
        **health,
        "engine": engine,
        "strategy_version": settings.strategy_version,
        "publish_timeframes": list(PUBLISH_TFS),
        "symbols": settings.symbols,
        "data_delayed": bool(health.get("degraded")),
        "message": (
            "Market data is delayed or incomplete. Signal generation is paused until the feeds "
            "recover — we would rather show you nothing than something wrong."
            if health.get("degraded")
            else "All feeds live."
        ),
    }


@router.get("/smart-money/{symbol}")
async def smart_money(symbol: str, session: AsyncSession = Depends(session_dep)):
    ctx = await smartmoney.load_context(session, symbol.upper())
    rows = await session.execute(
        text(
            """
            SELECT ts, net_flow_usd, accum_z, exchange_netflow_usd, cohort_size, source, is_simulated
            FROM smart_money_flow WHERE symbol = :symbol ORDER BY ts DESC LIMIT 90
            """
        ),
        {"symbol": symbol.upper()},
    )
    return {
        "symbol": symbol.upper(),
        "context": ctx.to_dict(),
        "series": [
            {**dict(r._mapping), "ts": r.ts.isoformat(),
             "net_flow_usd": float(r.net_flow_usd) if r.net_flow_usd is not None else None,
             "accum_z": float(r.accum_z) if r.accum_z is not None else None,
             "exchange_netflow_usd": float(r.exchange_netflow_usd) if r.exchange_netflow_usd is not None else None}
            for r in rows
        ],
        "disclosure": smartmoney.disclosure(),
    }


@router.get("/methodology")
async def methodology(session: AsyncSession = Depends(session_dep)):
    """What the engine is, in the same words as the docs, served as data."""
    from app.engine.scoring import MICRO_WEIGHT_BY_TF, STRATEGY_WEIGHTS
    from app.engine.strategies import REGIME_GATES

    versions = await session.execute(
        text("SELECT version, released_at, notes FROM strategy_versions ORDER BY released_at DESC")
    )
    return {
        "strategy_version": settings.strategy_version,
        "component_weights": STRATEGY_WEIGHTS,
        "orderflow_weight_by_timeframe": MICRO_WEIGHT_BY_TF,
        "regime_gates": REGIME_GATES,
        "min_risk_reward": settings.min_rr,
        "min_calibration_sample": settings.min_calibration_sample,
        "intrabar_rule": (
            "If a candle contains both the stop and a target, the stop is recorded as hit first. "
            "OHLC cannot establish ordering, so we take the pessimistic reading."
        ),
        "data_gates": {
            "min_live_venues": settings.min_live_venues,
            "max_book_staleness_ms": settings.max_book_staleness_ms,
            "max_trade_staleness_ms": settings.max_trade_staleness_ms,
        },
        "versions": [
            {"version": v.version, "released_at": v.released_at.isoformat(), "notes": v.notes}
            for v in versions
        ],
    }


# ───────────────────────────── added in v1.1 ────────────────────────────────

@router.get("/signals/{signal_id}/explain")
async def explain_signal(
    signal_id: str,
    user: CurrentUser | None = Depends(optional_user),
    session: AsyncSession = Depends(session_dep),
):
    """Plain-language rendering of the engine's structured output. Never a decision."""
    detail = await signal_detail(signal_id, user, session)
    return explain_svc.explain_signal(detail)


@router.get("/candidates")
async def candidates(session: AsyncSession = Depends(session_dep)):
    """The watchlist: setups the engine saw and chose not to publish, with reasons."""
    return {
        "candidates": await candidate_svc.open_candidates(session),
        "states": ["WATCHING", "SETUP_FORMING", "PROMOTED", "DROPPED"],
        "is_simulated": settings.is_replay,
        "explanation": (
            "WATCHING means there is directional evidence but the classification is NO TRADE — "
            "usually contradictory components or a counter-trend setup. SETUP FORMING means the "
            "classification is tradeable but a data, liquidity or risk gate blocked publication."
        ),
    }


@router.get("/market-intel")
async def market_intel():
    redis = get_redis()
    raw = await redis.get("sp:market_intel")
    if raw is None:
        raise HTTPException(503, "Market intelligence has not been computed yet; the engine may be warming up.")
    intel = json.loads(raw)
    intel["narrative"] = explain_svc.explain_market(intel)
    return intel


@router.get("/market/{symbol}/candles")
async def candles(symbol: str, timeframe: str = Query("1h", pattern="^(5m|15m|30m|1h|4h|1d)$"),
                  limit: int = Query(300, le=1000)):
    """Closed OHLCV bars for charting. The in-progress bar is excluded, matching the engine."""
    from app.market.adapters.binance import BinanceAdapter
    from app.market.adapters.replay import ReplayAdapter
    from app.features.candles import drop_unclosed
    import time as _time

    symbol = symbol.upper()
    if symbol not in settings.symbols:
        raise HTTPException(404, "Unknown market.")
    adapter = (ReplayAdapter(settings.symbols, speed=settings.replay_speed) if settings.is_replay
               else BinanceAdapter(settings.symbols))
    try:
        rows = await adapter.fetch_candles(symbol, timeframe, limit + 1)
    finally:
        await adapter.close()
    rows = drop_unclosed(rows, timeframe, int(_time.time() * 1000))[-limit:]
    return {"symbol": symbol, "timeframe": timeframe, "is_simulated": settings.is_replay,
            "candles": [{"time": r["ts"] // 1000, "open": r["open"], "high": r["high"],
                         "low": r["low"], "close": r["close"], "volume": r["volume"]} for r in rows]}


@router.get("/derivatives/{symbol}")
async def derivatives(symbol: str, session: AsyncSession = Depends(session_dep)):
    rows = await session.execute(text("""
        SELECT ts, source, funding_rate, open_interest, liq_long_usd, liq_short_usd, is_simulated
        FROM derivatives WHERE symbol = :symbol ORDER BY ts DESC LIMIT 240
    """), {"symbol": symbol.upper()})
    series = [{**dict(r._mapping), "ts": r.ts.isoformat(),
               "funding_rate": float(r.funding_rate) if r.funding_rate is not None else None,
               "open_interest": float(r.open_interest) if r.open_interest is not None else None,
               "liq_long_usd": float(r.liq_long_usd or 0), "liq_short_usd": float(r.liq_short_usd or 0)}
              for r in rows]
    return {"symbol": symbol.upper(), "latest": series[0] if series else None, "series": series,
            "role": "Funding and open interest feed the derivatives component (weight 0.10). "
                    "Liquidation skew is a risk flag, never a directional vote."}
