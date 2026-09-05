"""Market conditions score, smart-money validation gate, auth primitives."""
from __future__ import annotations

import numpy as np
import pytest

from app.core.security import (create_access_token, create_refresh_token, decode_token,
                               hash_password, verify_password)
from app.services import smartmoney
from app.services.market_intel import (CONDITION_WEIGHTS, AssetIntel, build_report,
                                       condition_components, conditions_score, correlation_matrix)


def asset(**kw) -> AssetIntel:
    base = dict(symbol="BTCUSDT", price=64000.0, trend_score=0.8, regime="trend_bull",
                regimes_by_tf={"1d": "trend_bull", "4h": "trend_bull", "1h": "trend_bull"}, rsi=62, macd_hist=5.0,
                vol_percentile=0.4, spread_z=0.2, obi_persistent=0.3, cvd_divergence=0.05, funding_rate=0.00005,
                oi_change_pct=1.0, liq_long_usd=0.0, liq_short_usd=0.0, smart_money_z=1.2, venues_live=3,
                staleness_ms=300)
    base.update(kw)
    return AssetIntel(**base)


def test_weights_sum_to_one_and_components_are_unit_scaled():
    assert sum(CONDITION_WEIGHTS.values()) == pytest.approx(1.0)
    comps = condition_components(asset())
    assert set(comps) == set(CONDITION_WEIGHTS)
    assert all(0.0 <= v <= 1.0 for v in comps.values())


def test_clean_trending_liquid_market_scores_high_and_degraded_market_low():
    good = conditions_score(condition_components(asset()))
    bad = conditions_score(condition_components(asset(trend_score=0.05, regimes_by_tf={"1d": "range", "4h": "trend_bear", "1h": "high_volatility"},
                                                       vol_percentile=0.98, spread_z=4.5, venues_live=1, staleness_ms=9000)))
    assert good > 75 and bad < 35


def test_score_is_direction_agnostic():
    up = conditions_score(condition_components(asset(trend_score=0.8, rsi=65, macd_hist=1)))
    down = conditions_score(condition_components(asset(trend_score=-0.8, rsi=35, macd_hist=-1)))
    assert up == down


def test_smart_money_carries_no_weight_in_the_score():
    a = conditions_score(condition_components(asset(smart_money_z=5.0)))
    b = conditions_score(condition_components(asset(smart_money_z=-5.0)))
    assert a == b


def test_correlation_matrix_is_symmetric_with_unit_diagonal():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.01, 60)
    rets = {"BTCUSDT": list(base), "ETHUSDT": list(base * 0.9 + rng.normal(0, 0.003, 60)),
            "SOLUSDT": list(rng.normal(0, 0.02, 60))}
    m = correlation_matrix(rets)
    assert m["BTCUSDT"]["BTCUSDT"] == pytest.approx(1.0)
    assert m["BTCUSDT"]["ETHUSDT"] == m["ETHUSDT"]["BTCUSDT"] > 0.8


def test_report_explains_itself():
    r = build_report([asset()], {"BTCUSDT": [0.001] * 30}, "2026-01-01T00:00:00+00:00", True)
    assert r["weights"] == CONDITION_WEIGHTS and "explanation" in r and r["is_simulated"] is True
    assert r["assets"][0]["score"] == r["overall_conditions_score"]


# ── smart money ─────────────────────────────────────────────────────────────

def _wallets(n_wallets=200, n_trades=40, skilled=(), seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n_wallets):
        edge = 0.6 if i in skilled else 0.0
        out.append(smartmoney.WalletCandidate(address=f"w{i}", chain="solana",
                                              returns=list(rng.normal(edge, 1.0, n_trades))))
    return out


def test_fdr_keeps_almost_nothing_from_pure_noise():
    cohort = smartmoney.build_cohort(_wallets(300, skilled=()), alpha=0.10)
    assert len(cohort) <= 6                       # a handful of false discoveries at most


def test_fdr_recovers_genuinely_skilled_wallets():
    skilled = set(range(5))
    cohort = smartmoney.build_cohort(_wallets(200, skilled=skilled), alpha=0.10)
    found = {c["address"] for c in cohort}
    assert len(found & {f"w{i}" for i in skilled}) >= 4


def test_excluded_labels_never_enter_the_cohort():
    rng = np.random.default_rng(2)
    w = smartmoney.WalletCandidate("cex", "ethereum", list(rng.normal(2.0, 0.5, 60)), label="exchange")
    assert smartmoney.build_cohort([w]) == []
    for label in ("market_maker", "copy_trader", "sybil", "mev_bot", "bridge"):
        w.label = label
        assert smartmoney.build_cohort([w]) == []


def test_wallet_score_penalises_regime_dependence_and_rewards_timing():
    rng = np.random.default_rng(3)
    r = list(rng.normal(0.5, 1.0, 60))
    balanced = smartmoney.score_wallet(smartmoney.WalletCandidate("a", "solana", r, regimes=["trend_bull", "range"] * 30,
                                                                    entry_lead_hours=[20.0] * 60))
    lopsided_returns = [x + 1.5 if i % 2 == 0 else x - 1.5 for i, x in enumerate(r)]
    lopsided = smartmoney.score_wallet(smartmoney.WalletCandidate("b", "solana", lopsided_returns,
                                                                    regimes=["trend_bull", "range"] * 30,
                                                                    entry_lead_hours=[0.0] * 60))
    assert balanced["regime_consistency"] > lopsided["regime_consistency"]
    assert balanced["timing_score"] > lopsided["timing_score"]


def test_oos_validation_passes_on_predictive_flow_and_fails_on_noise():
    rng = np.random.default_rng(4)
    select = _wallets(120, skilled=set(range(6)), seed=4)
    # Period B: cohort flow that genuinely leads returns.
    predictive = []
    for _ in range(80):
        signal = rng.normal(0, 1)
        flows = [(f"w{i}", signal * 1000 + rng.normal(0, 200)) for i in range(6)]
        predictive.append((flows, signal * 0.01 + rng.normal(0, 0.004)))
    noise = [([(f"w{i}", rng.normal(0, 1000)) for i in range(6)], rng.normal(0, 0.01)) for _ in range(80)]
    good = smartmoney.validate_cohort_oos(select, predictive)
    bad = smartmoney.validate_cohort_oos(select, noise)
    assert good["passed"] is True and good["oos_ic"] > 0.3
    assert bad["passed"] is False


def test_validation_with_empty_cohort_cannot_pass():
    r = smartmoney.validate_cohort_oos(_wallets(20, skilled=(), seed=9), [])
    assert r["passed"] is False


# ── security ────────────────────────────────────────────────────────────────

def test_password_hash_roundtrip_and_rejects_wrong_password():
    h = hash_password("correct horse battery")
    assert h != "correct horse battery"
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert not verify_password("anything", "not-a-real-hash")


def test_access_token_roundtrip_and_type_enforcement():
    token = create_access_token("user-1", "pro")
    payload = decode_token(token, "access")
    assert payload["sub"] == "user-1" and payload["tier"] == "pro"
    with pytest.raises(Exception):
        decode_token(token, "refresh")


def test_refresh_token_carries_a_unique_jti():
    t1, j1, _ = create_refresh_token("user-1")
    t2, j2, _ = create_refresh_token("user-1")
    assert j1 != j2 and decode_token(t1, "refresh")["jti"] == str(j1)


def test_tampered_token_is_rejected():
    token = create_access_token("user-1", "free")
    with pytest.raises(Exception):
        decode_token(token[:-3] + "abc", "access")
