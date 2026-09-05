"""Signal generation loop.

Once per cycle, for every symbol and publishable timeframe:

    fetch closed candles -> regimes per timeframe -> MTF view -> feature snapshot
    -> score -> classify (STRONG/WEAK/NO TRADE) -> gates -> risk plan
    -> calibrate -> publish -> alerts -> watchlist reconciliation

Only **closed** candles are scored. Exchange kline endpoints return the bar in
progress as the last row; scoring it is the most common way a live engine
quietly stops agreeing with its own backtest.

The engine is allowed — expected — to say NO TRADE. Every such decision is
recorded on the watchlist with its reasons, and cycle telemetry is published to
Redis so the status endpoint can show what the engine is doing and why.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import text

from app.config import PUBLISH_TFS, settings
from app.db import SessionLocal, close_connections, get_redis
from app.engine import publisher
from app.engine.calibration import empirical_bucket
from app.engine.risk import RiskRejection, build_risk_plan, run_gates
from app.engine.scoring import score_snapshot
from app.features.candles import drop_unclosed
from app.features.indicators import robust_z
from app.features.regime import build_mtf_view, classify_regime
from app.features.snapshot import (
    Candles,
    DataQuality,
    DerivFeatures,
    FeatureSnapshot,
    MicroFeatures,
    compute_ta_block,
    utcnow_iso,
)
from app.features.structure import analyse_structure
from app.market.adapters.binance import BinanceAdapter
from app.market.adapters.replay import ReplayAdapter
from app.services import alerts, candidates, market_intel, performance, smartmoney

log = logging.getLogger("signalproof.engine")

CYCLE_SECONDS = 60
CONTEXT_TFS = ("1d", "4h", "1h", "15m")
MIN_CANDLES = 210          # 200-EMA needs history before it means anything
STATS_KEY = "sp:engine:stats"
INTEL_KEY = "sp:market_intel"


class EngineWorker:
    def __init__(self) -> None:
        self.adapter = (
            ReplayAdapter(settings.symbols, settings.orderbook_depth, speed=settings.replay_speed)
            if settings.is_replay
            else BinanceAdapter(settings.symbols, settings.orderbook_depth, futures=True)
        )
        self.running = True
        self.stats: dict = {"cycles": 0, "published_total": 0}

    async def run(self) -> None:
        while self.running:
            started = time.monotonic()
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("engine cycle failed")
                self.stats["last_error"] = utcnow_iso()
            self.stats["last_cycle_ms"] = int((time.monotonic() - started) * 1000)
            await asyncio.sleep(CYCLE_SECONDS)

    # ── one pass over every market ────────────────────────────────────────

    async def cycle(self) -> None:
        redis = get_redis()
        health_raw = await redis.get("sp:health")
        health = json.loads(health_raw) if health_raw else {}
        counters: Counter = Counter()
        rejections: Counter = Counter()
        intel_assets: list[market_intel.AssetIntel] = []
        returns: dict[str, list[float]] = {}
        now_ms = int(time.time() * 1000)

        for symbol in settings.symbols:
            candles_by_tf: dict[str, Candles] = {}
            for tf in CONTEXT_TFS:
                rows = drop_unclosed(await self.adapter.fetch_candles(symbol, tf, 400), tf, now_ms)
                if len(rows) >= MIN_CANDLES:
                    candles_by_tf[tf] = _to_candles(rows)
            if "1h" not in candles_by_tf:
                log.warning("not enough closed candle history for %s; skipping", symbol)
                rejections["INSUFFICIENT_HISTORY"] += 1
                continue

            regimes = {tf: classify_regime(c.high, c.low, c.close) for tf, c in candles_by_tf.items()}
            mtf = build_mtf_view(regimes)
            micro = await self._micro_features(redis, symbol)
            quality = _quality_from(health, micro)
            liqs = await self._liquidations(redis, symbol)

            async with SessionLocal() as session:
                deriv = await self._deriv_features(session, symbol, liqs)
                context = await smartmoney.load_context(session, symbol)

                for tf in PUBLISH_TFS:
                    if tf not in candles_by_tf:
                        continue
                    outcome = await self._evaluate(
                        session, symbol, tf, candles_by_tf, regimes, mtf, micro, deriv, context, quality,
                    )
                    counters[outcome["result"]] += 1
                    if outcome.get("rejection"):
                        rejections[outcome["rejection"]] += 1
                await session.commit()

            # Market-intel inputs from the same closed candles.
            c1h = candles_by_tf["1h"]
            ta_1h = compute_ta_block(c1h)
            closes = np.asarray(c1h.close[-60:], dtype=float)
            returns[symbol] = list(np.diff(np.log(closes)))
            lead = regimes.get("4h", regimes["1h"])
            intel_assets.append(market_intel.AssetIntel(
                symbol=symbol,
                price=float(c1h.last_close),
                trend_score=lead.trend_score,
                regime=lead.regime,
                regimes_by_tf={tf: r.regime for tf, r in regimes.items()},
                rsi=ta_1h.get("rsi14"),
                macd_hist=ta_1h.get("macd_hist"),
                vol_percentile=ta_1h.get("vol_percentile"),
                spread_z=micro.spread_z if micro.available else None,
                obi_persistent=micro.obi_persistent if micro.available else None,
                cvd_divergence=micro.cvd_divergence if micro.available else None,
                funding_rate=deriv.funding_rate,
                oi_change_pct=deriv.oi_change_pct,
                liq_long_usd=deriv.liq_long_usd_1h or 0.0,
                liq_short_usd=deriv.liq_short_usd_1h or 0.0,
                smart_money_z=context.smart_money_accum_z if context.available else None,
                venues_live=quality.venues_live,
                staleness_ms=quality.max_staleness_ms,
            ))

        await redis.set(INTEL_KEY, json.dumps(market_intel.build_report(
            intel_assets, returns, utcnow_iso(), settings.is_replay,
        )), ex=300)

        self.stats.update({
            "cycles": self.stats["cycles"] + 1,
            "last_cycle_at": utcnow_iso(),
            "evaluated": sum(counters.values()),
            "published": counters["published"],
            "no_trade": counters["no_trade"],
            "blocked": counters["blocked"],
            "duplicate": counters["duplicate"],
            "rejections": dict(rejections),
            "published_total": self.stats["published_total"] + counters["published"],
            "strategy_version": settings.strategy_version,
        })
        await redis.set(STATS_KEY, json.dumps(self.stats), ex=600)
        if counters["published"]:
            await self._refresh_live_cache(redis)

    # ── one (symbol, timeframe) decision ──────────────────────────────────

    async def _evaluate(
        self, session, symbol, tf, candles_by_tf, regimes, mtf, micro, deriv, context, quality,
    ) -> dict:
        candles = candles_by_tf[tf]
        snapshot = FeatureSnapshot(
            symbol=symbol, timeframe=tf, taken_at=utcnow_iso(), candle_ts=candles.ts[-1],
            price=candles.last_close, ta=compute_ta_block(candles), regime=regimes[tf],
            regimes_by_tf=regimes, mtf=mtf,
            structure=analyse_structure(candles.high, candles.low, candles.close),
            micro=micro, deriv=deriv, context=context, quality=quality,
            is_simulated=settings.is_replay,
        )
        score = score_snapshot(snapshot)

        async def watch(blockers: list[str], signal_id: str | None = None) -> None:
            await candidates.observe(
                session, symbol, tf, score.direction, score.signal_class, score.strength,
                score.raw_score, score.conflict, blockers, signal_id, settings.is_replay,
            )

        if not score.tradeable:
            await watch(score.no_trade_reasons)
            return {"result": "no_trade", "rejection": "NO_TRADE"}

        gate = run_gates(snapshot, score.strength)
        if not gate.passed:
            log.info("%s %s blocked: %s — %s", symbol, tf, gate.code, gate.message)
            await watch([gate.message or gate.code or "gate"])
            return {"result": "blocked", "rejection": gate.code}

        try:
            plan = build_risk_plan(snapshot, score.direction, score.strength)
        except RiskRejection as exc:
            log.info("%s %s rejected by risk engine: %s", symbol, tf, exc.message)
            await watch([exc.message])
            return {"result": "blocked", "rejection": exc.code}

        outcomes = await performance.strength_outcomes(session, symbol, tf)
        calibration = empirical_bucket(score.strength, outcomes)

        try:
            result = await publisher.publish(session, snapshot, score, plan, calibration)
        except publisher.ValidationError as exc:
            log.error("%s %s failed validation: %s — %s", symbol, tf, exc.code, exc.message)
            return {"result": "blocked", "rejection": exc.code}

        if result is None:
            return {"result": "duplicate", "rejection": None}

        log.info("published %s %s %s strength=%d rr=%.2f id=%s",
                 symbol, tf, score.signal_class, score.strength, plan.risk_reward, result.signal_id)
        await watch([], signal_id=result.signal_id)
        await alerts.enqueue(session, {
            "signal_id": result.signal_id, "symbol": symbol, "timeframe": tf,
            "direction": score.direction, "strength": score.strength,
        })
        return {"result": "published", "rejection": None}

    # ── inputs ────────────────────────────────────────────────────────────

    async def _micro_features(self, redis, symbol: str) -> MicroFeatures:
        raw = await redis.get(f"sp:market:{symbol}")
        if not raw:
            return MicroFeatures(venue=settings.primary_venue, ts_ms=0, available=False)
        m = (json.loads(raw).get("micro") or {})
        if not m.get("available"):
            return MicroFeatures(venue=settings.primary_venue, ts_ms=0, available=False)
        allowed = set(MicroFeatures.__slots__)
        return MicroFeatures(**{k: v for k, v in m.items() if k in allowed and v is not None})

    async def _liquidations(self, redis, symbol: str) -> list[dict]:
        raw = await redis.get(f"sp:market:{symbol}")
        return (json.loads(raw).get("liquidations") or []) if raw else []

    async def _deriv_features(self, session, symbol: str, liqs: list[dict]) -> DerivFeatures:
        """Funding + open interest from the venue, liquidations from the hub.

        Each cycle's reading is persisted so change and robust-z features come
        from our own history rather than a vendor's derived number. Funding is
        compared against its own 30-day distribution; OI against its level
        roughly an hour ago.
        """
        funding = oi = None
        try:
            if hasattr(self.adapter, "fetch_funding"):
                funding = await self.adapter.fetch_funding(symbol)
            if hasattr(self.adapter, "fetch_open_interest"):
                oi = await self.adapter.fetch_open_interest(symbol)
        except Exception:  # noqa: BLE001
            pass
        if funding is None and oi is None:
            return DerivFeatures(available=False)

        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        cutoff_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
        liq_long = sum(l["notional"] for l in liqs if l["side"] == "long" and l["ts"] >= cutoff_ms)
        liq_short = sum(l["notional"] for l in liqs if l["side"] == "short" and l["ts"] >= cutoff_ms)
        source = "replay" if settings.is_replay else "binance_futures"

        await session.execute(text("""
            INSERT INTO derivatives (symbol, ts, source, funding_rate, open_interest,
                                     liq_long_usd, liq_short_usd, is_simulated)
            VALUES (:symbol, :ts, :source, :fr, :oi, :ll, :ls, :sim)
            ON CONFLICT (symbol, source, ts) DO NOTHING
        """), {"symbol": symbol, "ts": now, "source": source,
               "fr": funding.get("funding_rate") if funding else None, "oi": oi,
               "ll": liq_long, "ls": liq_short, "sim": settings.is_replay})

        hist = await session.execute(text("""
            SELECT ts, funding_rate, open_interest FROM derivatives
            WHERE symbol = :symbol AND source = :source AND ts >= :since
            ORDER BY ts
        """), {"symbol": symbol, "source": source, "since": now - timedelta(days=30)})
        rows = list(hist)

        funding_z = None
        fr_series = [float(r.funding_rate) for r in rows if r.funding_rate is not None]
        if len(fr_series) >= 20:
            z = robust_z(fr_series, lookback=len(fr_series))
            funding_z = float(z[-1]) if np.isfinite(z[-1]) else None

        oi_change = None
        if oi is not None:
            past = [float(r.open_interest) for r in rows
                    if r.open_interest is not None and r.ts <= now - timedelta(minutes=55)]
            if past and past[-1] > 0:
                oi_change = (oi - past[-1]) / past[-1] * 100.0

        return DerivFeatures(
            funding_rate=funding.get("funding_rate") if funding else None,
            funding_z=funding_z,
            oi_change_pct=oi_change,
            liq_long_usd_1h=liq_long,
            liq_short_usd_1h=liq_short,
            open_interest=oi,
            source=source,
            available=True,
        )

    async def _refresh_live_cache(self, redis) -> None:
        async with SessionLocal() as session:
            rows = await session.execute(text("""
                SELECT s.signal_id, s.symbol, s.direction, s.timeframe, s.strength, s.entry, s.stop_loss,
                       s.tp1, s.tp2, s.tp3, s.risk_reward, s.published_at, s.is_simulated, st.status,
                       s.features->>'signal_class' AS signal_class
                FROM signals s JOIN signal_state st ON st.signal_id = s.signal_id
                WHERE st.outcome IS NULL ORDER BY s.published_at DESC LIMIT 40
            """))
            payload = []
            for r in rows:
                d = dict(r._mapping)
                d["signal_id"] = str(d["signal_id"])
                d["published_at"] = d["published_at"].isoformat()
                for k in ("entry", "stop_loss", "tp1", "tp2", "tp3", "risk_reward"):
                    d[k] = float(d[k])
                payload.append(d)
        await redis.set("sp:signals:live", json.dumps(payload), ex=120)


def _to_candles(rows: list[dict]) -> Candles:
    return Candles(ts=[r["ts"] for r in rows], open=[r["open"] for r in rows],
                   high=[r["high"] for r in rows], low=[r["low"] for r in rows],
                   close=[r["close"] for r in rows], volume=[r["volume"] for r in rows])


def _quality_from(health: dict, micro: MicroFeatures) -> DataQuality:
    return DataQuality(
        venues_live=int(health.get("venues_live", 0)),
        book_synced=bool(micro.book_synced),
        max_staleness_ms=int(health.get("max_staleness_ms") or 0),
        clock_skew_ms=int(health.get("clock_skew_ms") or 0),
        degraded=bool(health.get("degraded", True)),
        flags=list(health.get("flags", [])),
    )


async def main() -> None:
    logging.basicConfig(level=settings.log_level.upper())
    log.info("engine worker starting — publishing on %s", ", ".join(PUBLISH_TFS))
    try:
        await EngineWorker().run()
    finally:
        await close_connections()


if __name__ == "__main__":
    asyncio.run(main())
