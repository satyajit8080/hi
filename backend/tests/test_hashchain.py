"""The immutability guarantees. If these fail, the product's core claim is void."""
from __future__ import annotations

import copy

import pytest

from app.core.hashchain import (
    GENESIS,
    canonical_payload,
    compute_row_hash,
    merkle_proof,
    merkle_root,
    verify_chain,
    verify_merkle_proof,
)


def make_signal(i: int, **overrides) -> dict:
    base = {
        "signal_id": f"0000000-0000-0000-0000-{i:012d}",
        "published_at": f"2026-01-0{(i % 9) + 1}T10:00:00+00:00",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "timeframe": "1h",
        "regime": "trend_bull",
        "strategy_version": "1.0.0",
        "strength": 70 + i,
        "mtf_alignment": 0.82,
        "entry": 64000.0 + i,
        "stop_loss": 63000.0,
        "tp1": 65000.0, "tp2": 66000.0, "tp3": 67000.0,
        "risk_reward": 2.0,
        "expires_at": "2026-01-02T10:00:00+00:00",
        "invalidation": "Close below 63000",
        "features": {"ta": {"rsi14": 55.5}},
        "reason_codes": [{"code": "EMA_STACK_BULL", "contribution": 0.11}],
        "risk_flags": [],
        "data_quality": {"venues_live": 2},
        "is_simulated": True,
    }
    base.update(overrides)
    return base


def build_chain(n: int) -> list[dict]:
    rows = []
    prev = GENESIS
    for i in range(n):
        s = make_signal(i)
        s["prev_hash"] = prev
        s["row_hash"] = compute_row_hash(s, prev)
        prev = s["row_hash"]
        rows.append(s)
    return rows


def test_canonical_payload_is_key_order_independent():
    a = make_signal(1)
    b = {k: a[k] for k in reversed(list(a))}
    assert canonical_payload(a) == canonical_payload(b)


def test_float_and_string_numbers_canonicalise_identically():
    a = make_signal(1, entry=64000.0)
    b = make_signal(1, entry=64000.00000000)
    assert compute_row_hash(a, GENESIS) == compute_row_hash(b, GENESIS)


def test_valid_chain_verifies():
    result = verify_chain(build_chain(25))
    assert result["ok"] is True
    assert result["checked"] == 25


def test_tampering_with_a_price_breaks_the_chain():
    rows = build_chain(10)
    tampered = copy.deepcopy(rows)
    tampered[4]["entry"] = 1.0            # rewrite history
    result = verify_chain(tampered)
    assert result["ok"] is False
    assert result["broken_at"] == tampered[4]["signal_id"]


def test_tampering_with_an_outcome_reason_breaks_the_chain():
    rows = build_chain(6)
    rows[2]["reason_codes"] = [{"code": "FABRICATED", "contribution": 0.9}]
    assert verify_chain(rows)["ok"] is False


def test_deleting_a_row_breaks_the_chain():
    rows = build_chain(8)
    del rows[3]
    assert verify_chain(rows)["ok"] is False


def test_reordering_breaks_the_chain():
    rows = build_chain(8)
    rows[2], rows[5] = rows[5], rows[2]
    assert verify_chain(rows)["ok"] is False


def test_merkle_root_stable_and_proofs_verify():
    leaves = [compute_row_hash(make_signal(i), GENESIS) for i in range(9)]
    root = merkle_root(leaves)
    assert root == merkle_root(list(leaves))
    for i in (0, 4, 8):
        assert verify_merkle_proof(leaves[i], merkle_proof(leaves, i), root)


def test_merkle_proof_fails_for_a_forged_leaf():
    leaves = [compute_row_hash(make_signal(i), GENESIS) for i in range(6)]
    root = merkle_root(leaves)
    forged = compute_row_hash(make_signal(99), GENESIS)
    assert verify_merkle_proof(forged, merkle_proof(leaves, 2), root) is False


def test_empty_chain_is_valid():
    assert verify_chain([])["ok"] is True
