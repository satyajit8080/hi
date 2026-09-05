"""Five-state classification, NO TRADE discipline, and determinism."""
from __future__ import annotations

import hashlib
import json

import pytest

from app.engine.scoring import (
    MAX_CONFLICT, STRONG_THRESHOLD, WEAK_THRESHOLD, ScoreResult, classify, evidence_conflict,
    score_snapshot,
)
from tests.conftest import make_candles, make_snapshot


def _result(**kw) -> ScoreResult:
    base = dict(direction="LONG", strength=75, raw_score=0.5, component_scores={}, reason_codes=[],
                risk_flags=[], strategy_outputs={}, mtf_alignment=0.8, is_counter_trend=False)
    base.update(kw)
    return ScoreResult(**base)


def test_strong_and_weak_thresholds():
    r = _result(strength=STRONG_THRESHOLD); classify(r); assert r.signal_class == "STRONG_LONG"
    r = _result(strength=STRONG_THRESHOLD - 1); classify(r); assert r.signal_class == "WEAK_LONG"
    r = _result(strength=WEAK_THRESHOLD - 1); classify(r); assert r.signal_class == "NO_TRADE"
    r = _result(direction="SHORT", raw_score=-0.5, strength=80); classify(r); assert r.signal_class == "STRONG_SHORT"


def test_no_direction_is_no_trade():
    r = _result(direction="NONE", raw_score=0.0, strength=0); classify(r)
    assert r.signal_class == "NO_TRADE" and r.no_trade_reasons


def test_contradictory_evidence_is_no_trade_even_when_strong():
    """+0.3 built from trend +0.8 and momentum -0.7 is a coin flip, not a signal."""
    r = _result(strength=85, conflict=MAX_CONFLICT + 0.05); classify(r)
    assert r.signal_class == "NO_TRADE"
    assert any("contradictory" in x for x in r.no_trade_reasons)


def test_counter_trend_is_demoted_to_watch():
    r = _result(strength=80, is_counter_trend=True); classify(r)
    assert r.signal_class == "NO_TRADE"
    assert any("1d/4h" in x for x in r.no_trade_reasons)


def test_two_high_severity_risks_block_a_trade():
    risks = [{"code": "A", "label": "a", "severity": "high"}, {"code": "B", "label": "b", "severity": "high"}]
    r = _result(strength=80, risk_flags=risks); classify(r)
    assert r.signal_class == "NO_TRADE"
    r2 = _result(strength=80, risk_flags=risks[:1]); classify(r2)
    assert r2.signal_class == "STRONG_LONG"


def test_evidence_conflict_measures_opposing_weight():
    agree = {"trend": 0.6, "momentum": 0.5, "structure": 0.4, "volume": 0.3, "volatility": 0.0,
             "order_flow": 0.0, "derivatives": 0.0}
    assert evidence_conflict(agree, 0.5) == 0.0
    split = {"trend": 0.8, "momentum": -0.7, "structure": 0.0, "volume": 0.0, "volatility": 0.0,
             "order_flow": 0.0, "derivatives": 0.0}
    c = evidence_conflict(split, 0.1)
    assert 0.4 < c < 0.5          # momentum 0.20 / (0.25 + 0.20)


def test_volatility_component_is_a_gate_not_a_vote_in_conflict():
    scores = {"trend": 0.6, "momentum": 0.4, "structure": 0.0, "volume": 0.0, "volatility": -0.4,
              "order_flow": 0.0, "derivatives": 0.0}
    assert evidence_conflict(scores, 0.3) == 0.0


def test_tradeable_property():
    r = _result(); classify(r); assert r.tradeable
    r = _result(direction="NONE", raw_score=0.0, strength=0); classify(r); assert not r.tradeable


# ── determinism ─────────────────────────────────────────────────────────────

def _fingerprint(result: ScoreResult) -> str:
    payload = {
        "direction": result.direction, "strength": result.strength, "raw": round(result.raw_score, 10),
        "class": result.signal_class, "conflict": round(result.conflict, 10),
        "components": {k: round(v, 10) for k, v in result.component_scores.items()},
        "reasons": [(r["code"], round(r["contribution"], 10)) for r in result.reason_codes],
        "risks": [r["code"] for r in result.risk_flags],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def test_scoring_is_deterministic_for_identical_input():
    snap = make_snapshot("1h", make_candles(trend=0.002, seed=17))
    assert _fingerprint(score_snapshot(snap)) == _fingerprint(score_snapshot(snap))


def test_scoring_is_deterministic_across_rebuilt_snapshots():
    a = _fingerprint(score_snapshot(make_snapshot("1h", make_candles(trend=0.002, seed=17))))
    b = _fingerprint(score_snapshot(make_snapshot("1h", make_candles(trend=0.002, seed=17))))
    assert a == b


def test_golden_regression_fixture():
    """Pin the exact output for a fixed input so a silent change in the engine
    cannot ship unnoticed. If this changes, bump the strategy version."""
    result = score_snapshot(make_snapshot("1h", make_candles(trend=0.001, noise=0.0025, seed=4)))
    assert result.direction == "LONG"
    assert result.signal_class == "WEAK_LONG"
    assert result.strength == pytest.approx(GOLDEN_STRENGTH)
    assert _fingerprint(result) == GOLDEN_FINGERPRINT


GOLDEN_STRENGTH = 46
GOLDEN_FINGERPRINT = "962cce6d4a17334c4ebff18a0dfaeb195d4763fb28bba189bbf38c7febe4b937"


def test_parabolic_move_is_not_chased():
    """RSI at 99 after 300 bars of +0.3%: the engine must decline, not chase."""
    result = score_snapshot(make_snapshot("1h", make_candles(trend=0.003, noise=0.002, seed=11)))
    assert result.direction == "LONG"
    assert result.signal_class == "NO_TRADE"
    assert any(r["code"] == "RSI_STRETCHED" for r in result.risk_flags)


def test_unavailable_data_is_excluded_from_the_denominator():
    """Unavailable is not neutral. With derivatives and order flow absent, the raw
    score must equal the weighted mean over the components that could vote —
    not the weighted mean over all seven with two silent zeros dragging it down."""
    from app.engine.scoring import STRATEGY_WEIGHTS
    snap = make_snapshot("1h", make_candles(trend=0.001, noise=0.0025, seed=4))
    assert not snap.deriv.available and not snap.micro.available
    r = score_snapshot(snap)
    voting = {k: w for k, w in STRATEGY_WEIGHTS.items() if k not in ("derivatives", "order_flow")}
    expected = sum(r.component_scores[k] * w for k, w in voting.items()) / sum(voting.values())
    assert r.raw_score == pytest.approx(expected, abs=1e-9)


def test_present_but_neutral_data_dilutes_the_score():
    """The flip side: evidence that was present and said little is a real vote."""
    from app.features.snapshot import DerivFeatures
    snap = make_snapshot("1h", make_candles(trend=0.001, noise=0.0025, seed=4))
    absent = score_snapshot(snap)
    snap.deriv = DerivFeatures(funding_rate=0.0, available=True)
    present = score_snapshot(snap)
    assert present.raw_score < absent.raw_score
    assert present.component_scores["derivatives"] > 0      # neutral funding is mildly supportive
