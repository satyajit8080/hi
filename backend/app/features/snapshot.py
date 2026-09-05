"""Builds the feature snapshot that is hashed into every published signal.

The snapshot is the audit artefact. If a user disputes a signal a year later,
this object plus the strategy version is enough to recompute the score, the
reason codes and the levels exactly — no access to our servers required.

Anything that cannot be recomputed from stored inputs does not belong here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.features import indicators as ind
from app.features.microstructure import BookSnapshot
from app.features.regime import MTFView, RegimeState
from app.features.structure import StructureState


@dataclass(slots=True)
class Candles:
    ts: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]

    def __len__(self) -> int:
        return len(self.close)

    @property
    def last_close(self) -> float:
        return self.close[-1]


@dataclass(slots=True)
class MicroFeatures:
    """Order-flow block. Only ever consumed by the entry-timing sub-model."""

    venue: str
    ts_ms: int
    best_bid: float = float("nan")
    best_ask: float = float("nan")
    mid: float = float("nan")
    micro_price: float = float("nan")
    spread_rel: float = float("nan")
    spread_z: float = 0.0
    obi_5: float = 0.0
    obi_20: float = 0.0
    obi_persistent: float = 0.0
    queue_imbalance: float = 0.0
    ofi: float = 0.0
    ofi_z: float = 0.0
    cvd_spot: float = 0.0
    cvd_perp: float = 0.0
    cvd_divergence: float = 0.0
    trade_imbalance: float = 0.0
    large_print_z: float = 0.0
    venues_agreeing: int = 0
    cancel_rate: float = 0.0          # share of level changes that were removals
    displayed_vs_executed: float = 0.0  # top-of-book notional / executed notional (60s)
    spoof_risk: float = 0.0           # 0..1, composite of the two above
    book_synced: bool = True
    available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DerivFeatures:
    funding_rate: float | None = None
    funding_z: float | None = None
    oi_change_pct: float | None = None
    liq_long_usd_1h: float | None = None
    liq_short_usd_1h: float | None = None
    open_interest: float | None = None
    source: str = "none"
    available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ContextFeatures:
    """Smart money / on-chain. CONTEXT ONLY unless a validation row says otherwise.

    Kept in its own block so it is impossible to accidentally sum into the score
    alongside the driver blocks.
    """

    smart_money_accum_z: float | None = None
    smart_money_cohort_size: int | None = None
    exchange_netflow_usd: float | None = None
    hyperliquid_whale_bias: float | None = None
    is_signal_driver: bool = False
    validation_ref: str | None = None
    available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DataQuality:
    venues_live: int
    book_synced: bool
    max_staleness_ms: int
    clock_skew_ms: int
    degraded: bool
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FeatureSnapshot:
    symbol: str
    timeframe: str
    taken_at: str
    candle_ts: int
    price: float
    ta: dict[str, float]
    regime: RegimeState
    regimes_by_tf: dict[str, RegimeState]
    mtf: MTFView
    structure: StructureState
    micro: MicroFeatures
    deriv: DerivFeatures
    context: ContextFeatures
    quality: DataQuality
    is_simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "taken_at": self.taken_at,
            "candle_ts": self.candle_ts,
            "price": round(self.price, 8),
            "ta": {k: (None if v is None or _nan(v) else round(float(v), 8)) for k, v in self.ta.items()},
            "regime": self.regime.to_dict(),
            "regimes_by_tf": {tf: r.to_dict() for tf, r in self.regimes_by_tf.items()},
            "mtf": {"biases": self.mtf.biases, "regimes": self.mtf.regimes},
            "structure": {
                "trend": self.structure.trend,
                "last_high": self.structure.last_high,
                "last_low": self.structure.last_low,
                "support": self.structure.support[:4],
                "resistance": self.structure.resistance[:4],
                "broke_structure": self.structure.broke_structure,
                "break_direction": self.structure.break_direction,
            },
            "micro": self.micro.to_dict(),
            "deriv": self.deriv.to_dict(),
            "context": self.context.to_dict(),
            "quality": self.quality.to_dict(),
            "is_simulated": self.is_simulated,
        }


def _nan(x: Any) -> bool:
    try:
        return bool(np.isnan(x))
    except (TypeError, ValueError):
        return False


def compute_ta_block(candles: Candles) -> dict[str, float]:
    """Indicator values at the last closed bar."""
    c = np.asarray(candles.close, dtype=float)
    h = np.asarray(candles.high, dtype=float)
    l = np.asarray(candles.low, dtype=float)
    v = np.asarray(candles.volume, dtype=float)
    i = len(c) - 1

    macd_line, macd_signal, macd_hist = ind.macd(c)
    adx_v, plus_di, minus_di = ind.adx(h, l, c)
    bb_up, bb_mid, bb_low = ind.bollinger(c)
    rsi14 = ind.rsi(c, 14)
    atr14 = ind.atr(h, l, c, 14)
    vol_sma = ind.sma(v, 20)

    def at(arr) -> float:
        val = arr[i]
        return float("nan") if val is None else float(val)

    volume_ratio = float(v[i] / vol_sma[i]) if vol_sma[i] and not np.isnan(vol_sma[i]) else float("nan")

    return {
        "close": float(c[i]),
        "ema20": at(ind.ema(c, 20)),
        "ema50": at(ind.ema(c, 50)),
        "ema200": at(ind.ema(c, 200)),
        "rsi14": at(rsi14),
        "rsi14_prev": float(rsi14[i - 1]) if i >= 1 else float("nan"),
        "macd": at(macd_line),
        "macd_signal": at(macd_signal),
        "macd_hist": at(macd_hist),
        "macd_hist_prev": float(macd_hist[i - 1]) if i >= 1 else float("nan"),
        "adx": at(adx_v),
        "plus_di": at(plus_di),
        "minus_di": at(minus_di),
        "atr14": at(atr14),
        "atr_pct": float(atr14[i] / c[i]) if not np.isnan(atr14[i]) and c[i] else float("nan"),
        "bb_upper": at(bb_up),
        "bb_mid": at(bb_mid),
        "bb_lower": at(bb_low),
        "bb_width": at(ind.bollinger_width(c)),
        "bb_width_pct": at(ind.percentile_rank(ind.bollinger_width(c), 200)),
        "realized_vol": at(ind.realized_volatility(c, 20)),
        "vol_percentile": at(ind.percentile_rank(ind.realized_volatility(c, 20), 200)),
        "volume_ratio": volume_ratio,
        "obv_slope": _slope(ind.obv(c, v), 10),
        "roc10": at(ind.rate_of_change(c, 10)),
        "vwap": at(ind.vwap(h, l, c, v)),
    }


def _slope(arr, window: int) -> float:
    a = np.asarray(arr, dtype=float)
    if len(a) <= window:
        return float("nan")
    y = a[-window:]
    if np.isnan(y).any():
        return float("nan")
    x = np.arange(window, dtype=float)
    denom = float(((x - x.mean()) ** 2).sum())
    if denom <= 0:
        return float("nan")
    slope = float(((x - x.mean()) * (y - y.mean())).sum() / denom)
    scale = float(np.abs(y).mean()) or 1.0
    return slope / scale


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
