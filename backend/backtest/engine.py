"""Event-driven backtester.

Deliberately pessimistic, because a backtest that flatters the strategy is worse
than no backtest:

  * **SL-first intrabar rule** — if a bar's range contains both stop and target,
    the stop is taken. Identical to the live lifecycle rule.
  * **Costs are always charged** — taker fee plus slippage on entry and exit.
  * **Signals evaluate on closed bars only** — no peeking at the bar in
    progress, matching the live engine exactly.
  * **Order-flow features are unavailable here.** They cannot be reconstructed
    from OHLCV, so backtests run with `micro.available = False` and the engine
    scores without them. That is the honest position: the order-flow track
    record starts when live recording starts, and the UI says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.engine.risk import EXPIRY_BY_TF, RiskRejection, build_risk_plan, strength_gate
from app.engine.scoring import score_snapshot
from app.features.regime import build_mtf_view, classify_regime
from app.features.snapshot import (
    Candles,
    ContextFeatures,
    DataQuality,
    DerivFeatures,
    FeatureSnapshot,
    MicroFeatures,
    compute_ta_block,
)
from app.features.structure import analyse_structure

DEFAULT_FEE_BPS = 5.0        # taker, each side
DEFAULT_SLIPPAGE_BPS = 3.0   # each side


@dataclass
class BacktestConfig:
    symbol: str
    timeframe: str
    fee_bps: float = DEFAULT_FEE_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    spread_bps: float = 1.5           # half-spread paid on each side
    min_strength: int = 45
    warmup: int = 210
    max_open: int = 1
    charge_funding: bool = True
    funding_bps_per_8h: float = 1.0
    # Ablation toggles. A feature is only *available* to the engine when both
    # the toggle is on and recorded data exists for that bar — order flow can
    # never be conjured from OHLCV, so C/D/E silently collapse to A/B without a
    # feature store and the report says so.
    use_derivatives: bool = False
    use_orderflow: bool = False
    use_smartmoney: bool = False
    require_no_trade_class: bool = True   # respect STRONG/WEAK/NO_TRADE classification


class FeatureStore:
    """Recorded per-bar features keyed by candle timestamp (ms).

    Built from the `microstructure`, `derivatives` and `smart_money_flow`
    tables, or from a JSON export of them. The backtester looks up the bar
    being scored and, only if a record exists at or before that timestamp within
    one bar, hands it to the engine. Nothing here can look forward.
    """

    def __init__(self, micro: dict[int, dict] | None = None, deriv: dict[int, dict] | None = None,
                 context: dict[int, dict] | None = None, tolerance_ms: int = 3_600_000) -> None:
        self.micro = dict(sorted((micro or {}).items()))
        self.deriv = dict(sorted((deriv or {}).items()))
        self.context = dict(sorted((context or {}).items()))
        self.tolerance_ms = tolerance_ms

    @staticmethod
    def _lookup(table: dict[int, dict], ts: int, tol: int) -> dict | None:
        best = None
        for k in table:
            if k <= ts and ts - k <= tol:
                best = table[k]
            elif k > ts:
                break
        return best

    def micro_at(self, ts: int) -> dict | None:
        return self._lookup(self.micro, ts, self.tolerance_ms)

    def deriv_at(self, ts: int) -> dict | None:
        return self._lookup(self.deriv, ts, self.tolerance_ms)

    def context_at(self, ts: int) -> dict | None:
        return self._lookup(self.context, ts, self.tolerance_ms)

    @property
    def empty(self) -> bool:
        return not (self.micro or self.deriv or self.context)


@dataclass
class OpenTrade:
    direction: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    opened_at: datetime
    expires_at: datetime
    strength: int
    regime: str
    tp_hits: int = 0
    mfe: float = 0.0
    mae: float = 0.0
    bars_held: int = 0
    reasons: list[str] = field(default_factory=list)
    signal_class: str = ""


class Backtester:
    def __init__(self, config: BacktestConfig, features: FeatureStore | None = None) -> None:
        self.cfg = config
        self.features = features or FeatureStore()
        self.trades: list[dict] = []
        self.open: OpenTrade | None = None
        self.skipped: dict[str, int] = {}
        self.feature_coverage = {"orderflow": 0, "derivatives": 0, "smartmoney": 0, "bars": 0}

    def _cost_pct(self) -> float:
        # Fee + slippage + half-spread, each paid twice (entry and exit).
        return (self.cfg.fee_bps + self.cfg.slippage_bps + self.cfg.spread_bps) * 2 / 10_000.0 * 100.0

    def run(self, candles: Candles, htf: dict[str, Candles] | None = None) -> list[dict]:
        htf = htf or {}
        n = len(candles)
        for i in range(self.cfg.warmup, n):
            window = _slice(candles, i)
            bar_ts = datetime.fromtimestamp(candles.ts[i] / 1000, tz=timezone.utc)

            if self.open is not None:
                self._advance(bar_ts, candles.high[i], candles.low[i], candles.close[i])

            if self.open is not None:
                continue

            snapshot = self._snapshot(window, htf, i, bar_ts)
            score = score_snapshot(snapshot)
            if score.direction == "NONE":
                continue
            if self.cfg.require_no_trade_class and not score.tradeable:
                self._skip("NO_TRADE")
                continue
            gate = strength_gate(score.strength, self.cfg.min_strength)
            if not gate.passed:
                self._skip(gate.code)
                continue
            try:
                plan = build_risk_plan(snapshot, score.direction, score.strength)
            except RiskRejection as exc:
                self._skip(exc.code)
                continue

            self.open = OpenTrade(
                direction=score.direction,
                entry=plan.entry,
                stop=plan.stop_loss,
                tp1=plan.tp1, tp2=plan.tp2, tp3=plan.tp3,
                opened_at=bar_ts,
                expires_at=bar_ts + EXPIRY_BY_TF.get(self.cfg.timeframe, timedelta(hours=6)),
                strength=score.strength,
                regime=snapshot.regime.regime,
                reasons=[r["code"] for r in score.reason_codes if r.get("role") == "driver"][:5],
            )
            self.open.signal_class = score.signal_class

        if self.open is not None:
            self._close(self.open, candles.close[-1], "EXPIRED",
                        datetime.fromtimestamp(candles.ts[-1] / 1000, tz=timezone.utc),
                        "Backtest window ended with the position open.")
        return self.trades

    def _snapshot(self, window: Candles, htf: dict, i: int, ts: datetime) -> FeatureSnapshot:
        regimes = {self.cfg.timeframe: classify_regime(window.high, window.low, window.close)}
        for tf, c in htf.items():
            aligned = _align(c, window.ts[-1])
            if aligned and len(aligned) > 210:
                regimes[tf] = classify_regime(aligned.high, aligned.low, aligned.close)
        bar_ts = window.ts[-1]
        self.feature_coverage["bars"] += 1

        # Order flow cannot be reconstructed from OHLCV. It is only available
        # when a recorded observation exists for this bar and the toggle is on.
        micro = MicroFeatures(venue="backtest", ts_ms=bar_ts, available=False)
        if self.cfg.use_orderflow:
            rec = self.features.micro_at(bar_ts)
            if rec:
                allowed = set(MicroFeatures.__slots__)
                micro = MicroFeatures(**{k: v for k, v in rec.items() if k in allowed and v is not None},
                                      venue="recorded", ts_ms=bar_ts, available=True)
                self.feature_coverage["orderflow"] += 1

        deriv = DerivFeatures(available=False)
        if self.cfg.use_derivatives:
            rec = self.features.deriv_at(bar_ts)
            if rec:
                allowed = set(DerivFeatures.__slots__)
                deriv = DerivFeatures(**{k: v for k, v in rec.items() if k in allowed}, available=True)
                self.feature_coverage["derivatives"] += 1

        context = ContextFeatures(available=False)
        if self.cfg.use_smartmoney:
            rec = self.features.context_at(bar_ts)
            if rec:
                allowed = set(ContextFeatures.__slots__)
                context = ContextFeatures(**{k: v for k, v in rec.items() if k in allowed}, available=True)
                self.feature_coverage["smartmoney"] += 1

        return FeatureSnapshot(
            symbol=self.cfg.symbol,
            timeframe=self.cfg.timeframe,
            taken_at=ts.isoformat(),
            candle_ts=bar_ts,
            price=window.close[-1],
            ta=compute_ta_block(window),
            regime=regimes[self.cfg.timeframe],
            regimes_by_tf=regimes,
            mtf=build_mtf_view(regimes),
            structure=analyse_structure(window.high, window.low, window.close),
            micro=micro,
            deriv=deriv,
            context=context,
            quality=DataQuality(venues_live=99, book_synced=True, max_staleness_ms=0,
                                clock_skew_ms=0, degraded=False, flags=[]),
            is_simulated=True,
        )

    def _advance(self, ts: datetime, high: float, low: float, close: float) -> None:
        t = self.open
        assert t is not None
        t.bars_held += 1

        favourable = high if t.direction == "LONG" else low
        adverse = low if t.direction == "LONG" else high
        t.mfe = max(t.mfe, _move(t.direction, t.entry, favourable))
        t.mae = min(t.mae, _move(t.direction, t.entry, adverse))

        stop_hit = low <= t.stop if t.direction == "LONG" else high >= t.stop
        for n, level in ((1, t.tp1), (2, t.tp2), (3, t.tp3)):
            hit = high >= level if t.direction == "LONG" else low <= level
            if hit and n > t.tp_hits and not stop_hit:
                t.tp_hits = n

        if stop_hit:
            # Pessimistic: the stop resolves first whenever both are in range.
            self._close(t, t.stop, "WIN" if t.tp_hits >= 1 else "LOSS", ts,
                        "Stop reached (stop-first rule applied).")
            return
        if t.tp_hits == 3:
            self._close(t, t.tp3, "WIN", ts, "All targets reached.")
            return
        if ts >= t.expires_at:
            self._close(t, close, "WIN" if t.tp_hits >= 1 else "EXPIRED", ts, "Validity elapsed.")

    def _close(self, t: OpenTrade, price: float, outcome: str, ts: datetime, reason: str) -> None:
        gross = _move(t.direction, t.entry, price) * 100.0
        cost = self._cost_pct()
        if self.cfg.charge_funding:
            hours = max((ts - t.opened_at).total_seconds() / 3600.0, 0.0)
            cost += (hours / 8.0) * self.cfg.funding_bps_per_8h / 100.0
        net = gross - cost
        risk = abs(t.entry - t.stop)

        self.trades.append({
            "opened_at": t.opened_at.isoformat(),
            "closed_at": ts.isoformat(),
            "direction": t.direction,
            "entry": t.entry, "stop": t.stop, "exit": price,
            "outcome": outcome,
            "pnl_pct": round(net, 6),
            "gross_pnl_pct": round(gross, 6),
            "cost_pct": round(cost, 6),
            "r_multiple": round((price - t.entry) / risk * (1 if t.direction == "LONG" else -1), 4)
            if risk > 0 else None,
            "tp_hits": t.tp_hits,
            "mfe_pct": round(t.mfe * 100, 4),
            "mae_pct": round(t.mae * 100, 4),
            "bars_held": t.bars_held,
            "strength": t.strength,
            "signal_class": t.signal_class,
            "regime": t.regime,
            "reasons": t.reasons,
            "close_reason": reason,
        })
        self.open = None

    def _skip(self, code: str | None) -> None:
        if code:
            self.skipped[code] = self.skipped.get(code, 0) + 1


def _move(direction: str, entry: float, price: float) -> float:
    m = (price - entry) / entry
    return m if direction == "LONG" else -m


def _slice(c: Candles, i: int) -> Candles:
    return Candles(c.ts[: i + 1], c.open[: i + 1], c.high[: i + 1],
                   c.low[: i + 1], c.close[: i + 1], c.volume[: i + 1])


def _align(c: Candles, ts: int) -> Candles | None:
    """Higher-timeframe bars closed at or before `ts` — never after."""
    idx = [i for i, t in enumerate(c.ts) if t <= ts]
    if not idx:
        return None
    j = idx[-1]
    return Candles(c.ts[: j + 1], c.open[: j + 1], c.high[: j + 1],
                   c.low[: j + 1], c.close[: j + 1], c.volume[: j + 1])
