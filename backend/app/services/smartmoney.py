"""Smart-money / on-chain context.

The research was blunt about this: for BTC, ETH and SOL — the three most
institutionally traded assets in crypto — wallet-level "smart money" is weak at
1h-1d horizons. It is genuinely useful on memecoins and low caps, and mostly
narrative on majors. So it ships here as **context**, displayed but not scored.

Promotion requires evidence, not a config flag:

  1. Select a wallet cohort on period A using FDR-controlled t-statistics.
  2. Test whether that cohort's period-B flow predicts period-B returns.
  3. Only a row in `smart_money_validation` with `passed = true` lets the
     feature carry weight, and `SP_SMART_MONEY_IS_SIGNAL_DRIVER` must also be
     on. Either alone does nothing.

Hyperliquid is the one honest exception for majors: positions there are public
on-chain, so large-trader positioning is directly observable rather than
inferred. It is still surfaced as context in V1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.features.snapshot import ContextFeatures

CHAIN_BY_SYMBOL = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana"}


EXCLUDED_LABELS = ("exchange", "market_maker", "bridge", "mev_bot", "mixer",
                   "copy_trader", "sybil", "infrastructure", "contract")


@dataclass(slots=True)
class WalletCandidate:
    address: str
    chain: str
    returns: list[float]                   # per-trade excess returns vs the asset
    label: str | None = None
    holding_hours: list[float] | None = None
    regimes: list[str] | None = None       # regime each trade was opened in
    entry_lead_hours: list[float] | None = None  # hours before the move the wallet entered


def benjamini_hochberg(p_values: list[float], alpha: float = 0.10) -> list[bool]:
    """FDR control. Testing thousands of wallets guarantees lucky ones; this is
    the correction that keeps them out of the cohort."""
    n = len(p_values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: p_values[i])
    keep = [False] * n
    max_i = -1
    for rank, idx in enumerate(order, start=1):
        if p_values[idx] <= alpha * rank / n:
            max_i = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= max_i:
            keep[idx] = True
    return keep


def score_wallet(candidate: WalletCandidate, min_trades: int = 30) -> dict | None:
    """t-statistic on a wallet's excess returns, plus a bounded skill score.

    A wallet with three wins is luck. The `min_trades` floor is the cheapest
    defence against a cohort made entirely of survivors.
    """
    r = np.asarray(candidate.returns, dtype=float)
    n = len(r)
    if n < min_trades:
        return None
    mean = float(r.mean())
    sd = float(r.std(ddof=1))
    if sd <= 0:
        return None
    t_stat = mean / (sd / math.sqrt(n))
    # Two-sided normal approximation; adequate at n >= 30.
    p_value = 2.0 * (1.0 - _norm_cdf(abs(t_stat)))
    win_rate = float((r > 0).mean())

    equity = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate(([0.0], equity)))
    max_dd = float((peak[1:] - equity).max())
    calmar = float(r.sum() / max_dd) if max_dd > 0 else float(r.sum() > 0) * 5.0

    # Consistency across regimes: a wallet that only wins in one regime is a
    # regime bet, not skill. Score the worst regime it has >= 8 trades in.
    regime_consistency = 1.0
    if candidate.regimes and len(candidate.regimes) == n:
        by_regime: dict[str, list[float]] = {}
        for rg, ret in zip(candidate.regimes, r):
            by_regime.setdefault(rg, []).append(ret)
        worst = [float(np.mean(v)) for v in by_regime.values() if len(v) >= 8]
        if worst:
            regime_consistency = float(np.clip(0.5 + min(worst) / (abs(mean) + 1e-9) * 0.5, 0.0, 1.0))

    # Timing: entering ahead of the move is skill; entering after it is a copy.
    timing = 0.5
    if candidate.entry_lead_hours:
        timing = float(np.clip(np.mean(candidate.entry_lead_hours) / 24.0, 0.0, 1.0))

    hold = float(np.median(candidate.holding_hours)) if candidate.holding_hours else None

    score = (
        float(np.clip(t_stat / 6.0, 0.0, 1.0)) * 0.45
        + float(np.clip(calmar / 10.0, 0.0, 1.0)) * 0.20
        + regime_consistency * 0.20
        + timing * 0.15
    ) * float(np.clip(n / 100.0, 0.3, 1.0))
    return {
        "address": candidate.address,
        "chain": candidate.chain,
        "n_trades": n,
        "win_rate": win_rate,
        "mean_excess": mean,
        "t_stat": t_stat,
        "p_value": p_value,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "regime_consistency": regime_consistency,
        "timing_score": timing,
        "median_holding_hours": hold,
        "score": float(np.clip(score, 0.0, 1.0)),
        "label": candidate.label,
    }


def build_cohort(
    candidates: list[WalletCandidate],
    min_trades: int = 30,
    alpha: float = 0.10,
    exclude_labels: tuple[str, ...] = EXCLUDED_LABELS,
) -> list[dict]:
    """Score, exclude known non-alpha entities, then FDR-filter what remains."""
    scored = []
    for c in candidates:
        if c.label in exclude_labels:
            continue
        s = score_wallet(c, min_trades)
        if s:
            scored.append(s)
    if not scored:
        return []
    keep = benjamini_hochberg([s["p_value"] for s in scored], alpha)
    n = len(scored)
    for s, k in zip(scored, keep):
        s["selected"] = bool(k and s["t_stat"] > 0)
        s["q_value"] = min(1.0, s["p_value"] * n / max(1, sum(keep)))
    return [s for s in scored if s["selected"]]


def cohort_flow_feature(
    flows: list[tuple[float, float]], history: list[float]
) -> float | None:
    """Score-weighted net flow, expressed as a robust z against its own history.

    `flows` are (wallet_score, signed_usd) pairs. Median/MAD scaling is used
    because on-chain flow distributions are heavy-tailed enough that a plain
    z-score is dominated by a handful of days.
    """
    if not flows:
        return None
    net = sum(score * usd for score, usd in flows)
    hist = np.asarray(history, dtype=float)
    if len(hist) < 20:
        return None
    med = float(np.median(hist))
    mad = float(np.median(np.abs(hist - med)))
    scale = 1.4826 * mad
    if scale < 1e-9:
        return 0.0
    return float(np.clip((net - med) / scale, -6, 6))


def spearman_ic(feature: list[float], forward_returns: list[float]) -> tuple[float, float, int]:
    """Rank information coefficient with a normal-approximation p-value."""
    x = np.asarray(feature, dtype=float)
    y = np.asarray(forward_returns, dtype=float)
    n = min(len(x), len(y))
    if n < 20:
        return 0.0, 1.0, n
    x, y = x[-n:], y[-n:]
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    if rx.std() == 0 or ry.std() == 0:
        return 0.0, 1.0, n
    ic = float(np.corrcoef(rx, ry)[0, 1])
    t_stat = ic * math.sqrt((n - 2) / max(1e-12, 1 - ic * ic))
    p = 2.0 * (1.0 - _norm_cdf(abs(t_stat)))
    return ic, p, n


def validate_cohort_oos(
    select_candidates: list[WalletCandidate],
    validate_flow_by_period: list[tuple[list[tuple[str, float]], float]],
    min_ic: float = 0.05,
    max_p: float = 0.05,
) -> dict:
    """The promotion test, in one function.

    1. Build the cohort on period A only (`select_candidates`).
    2. On period B, for each observation, compute the score-weighted cohort net
       flow from `(address, signed_usd)` pairs and pair it with the forward return.
    3. The feature is promoted only if the rank IC on period B is positive,
       above `min_ic`, and significant at `max_p`.

    Period B is never used to choose the cohort. That separation is the entire
    point; without it every wallet cohort "predicts" the period it was mined from.
    """
    cohort = build_cohort(select_candidates)
    weights = {c["address"]: c["score"] for c in cohort}
    if not weights:
        return {"passed": False, "wallets_selected": 0, "oos_ic": None, "oos_p_value": None,
                "n_obs": 0, "note": "No wallet survived selection on period A."}

    feature: list[float] = []
    forward: list[float] = []
    for flows, fwd in validate_flow_by_period:
        net = sum(weights.get(addr, 0.0) * usd for addr, usd in flows)
        feature.append(net)
        forward.append(fwd)

    ic, p, n = spearman_ic(feature, forward)
    passed = bool(ic >= min_ic and p <= max_p and n >= 30)
    return {
        "passed": passed,
        "wallets_selected": len(weights),
        "oos_ic": round(ic, 5),
        "oos_p_value": round(p, 6),
        "n_obs": n,
        "note": ("Cohort flow predicts forward returns out of sample." if passed else
                 "Cohort flow does not predict forward returns out of sample; stays context-only."),
    }


async def record_validation(session: AsyncSession, cohort_id: str, chain: str, symbol: str,
                            select_range: tuple, validate_range: tuple, result: dict) -> None:
    await session.execute(text("""
        INSERT INTO smart_money_validation (cohort_id, chain, symbol, select_start, select_end,
            validate_start, validate_end, wallets_selected, oos_ic, oos_p_value, passed, notes)
        VALUES (:cid, :chain, :symbol, :ss, :se, :vs, :ve, :n, :ic, :p, :passed, :notes)
    """), {"cid": cohort_id, "chain": chain, "symbol": symbol, "ss": select_range[0], "se": select_range[1],
           "vs": validate_range[0], "ve": validate_range[1], "n": result["wallets_selected"],
           "ic": result["oos_ic"], "p": result["oos_p_value"], "passed": result["passed"], "notes": result["note"]})


async def validation_status(session: AsyncSession, symbol: str) -> tuple[bool, str | None]:
    """Has this symbol's cohort earned the right to move the score?"""
    row = await session.execute(
        text(
            """
            SELECT cohort_id, passed, oos_ic, oos_p_value, evaluated_at
            FROM smart_money_validation
            WHERE symbol = :symbol
            ORDER BY evaluated_at DESC
            LIMIT 1
            """
        ),
        {"symbol": symbol},
    )
    r = row.first()
    if r is None:
        return False, None
    return bool(r.passed), r.cohort_id


async def load_context(session: AsyncSession, symbol: str) -> ContextFeatures:
    if not settings.smart_money_enabled:
        return ContextFeatures(available=False)

    passed, cohort_id = await validation_status(session, symbol)
    # Both conditions must hold. A config flag alone can never promote it.
    is_driver = bool(passed and settings.smart_money_is_signal_driver)

    row = await session.execute(
        text(
            """
            SELECT accum_z, cohort_size, exchange_netflow_usd
            FROM smart_money_flow
            WHERE symbol = :symbol
            ORDER BY ts DESC
            LIMIT 1
            """
        ),
        {"symbol": symbol},
    )
    r = row.first()
    if r is None:
        return ContextFeatures(is_signal_driver=is_driver, validation_ref=cohort_id, available=False)

    return ContextFeatures(
        smart_money_accum_z=float(r.accum_z) if r.accum_z is not None else None,
        smart_money_cohort_size=int(r.cohort_size) if r.cohort_size is not None else None,
        exchange_netflow_usd=float(r.exchange_netflow_usd) if r.exchange_netflow_usd is not None else None,
        hyperliquid_whale_bias=None,
        is_signal_driver=is_driver,
        validation_ref=cohort_id,
        available=True,
    )


class HyperliquidClient:
    """Public, keyless. `clearinghouseState` exposes any address's positions,
    which makes large-trader positioning directly observable rather than
    inferred — the one on-chain source that is genuinely legible for majors."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.hyperliquid_api_url
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)

    async def clearinghouse_state(self, address: str) -> dict | None:
        try:
            r = await self._client.post("/info", json={"type": "clearinghouseState", "user": address})
            return r.json() if r.status_code == 200 else None
        except httpx.HTTPError:
            return None

    async def whale_bias(self, addresses: list[str], coin: str) -> float | None:
        """Net long/short lean of tracked addresses in one coin, in [-1, 1]."""
        long_usd = short_usd = 0.0
        seen = 0
        for addr in addresses[:40]:
            state = await self.clearinghouse_state(addr)
            if not state:
                continue
            seen += 1
            for pos in state.get("assetPositions", []):
                p = pos.get("position", {})
                if p.get("coin") != coin:
                    continue
                value = abs(float(p.get("positionValue", 0) or 0))
                if float(p.get("szi", 0) or 0) > 0:
                    long_usd += value
                else:
                    short_usd += value
        if seen == 0 or long_usd + short_usd == 0:
            return None
        return (long_usd - short_usd) / (long_usd + short_usd)

    async def close(self) -> None:
        await self._client.aclose()


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def disclosure() -> dict:
    """Rendered verbatim on the smart-money page. If we show it, we say what it is."""
    return {
        "role": "context_only" if not settings.smart_money_is_signal_driver else "driver_pending_validation",
        "explanation": (
            "On-chain and whale data is shown for context. It does not move the signal score "
            "for BTC, ETH or SOL. Wallet-level flow has a well-documented edge on small caps "
            "and a weak, largely narrative one on majors, where exchange custody, ETFs and "
            "market makers dominate the flow. It is promoted to a scoring input only if a "
            "cohort selected on one period predicts returns in a later, untouched period."
        ),
        "promotion_requirements": [
            "Cohort selected with a minimum of 30 closed trades per wallet",
            "Benjamini-Hochberg FDR control across all wallets tested",
            "Exchange, market-maker, bridge and MEV addresses excluded",
            "Out-of-sample information coefficient significant on a held-out period",
            "A passing row recorded in smart_money_validation",
        ],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
