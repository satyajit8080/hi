"""Tamper-evident signal ledger primitives.

Two independent guarantees:

1. **Chain** — every published signal stores
   ``row_hash = sha256(canonical(payload) || prev_hash)``. Changing any historic
   field breaks that row's hash and every hash after it. Verifying the whole
   chain is one linear pass and is exposed publicly at ``/v1/verify/chain``.

2. **Anchor** — the day's ``row_hash`` values form a Merkle tree whose root is
   published (and optionally timestamped into Bitcoin via OpenTimestamps). That
   pins the chain to a point in time that we do not control, which is what makes
   "we didn't backdate this" checkable by a stranger.

Canonicalisation matters more than the hash function: two parties must agree
byte-for-byte on what was hashed, so we sort keys, force compact separators, and
render every number as a fixed-precision decimal string.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

GENESIS = "0" * 64
HASH_FIELDS = (
    "signal_id",
    "published_at",
    "symbol",
    "direction",
    "timeframe",
    "regime",
    "strategy_version",
    "strength",
    "mtf_alignment",
    "entry",
    "stop_loss",
    "tp1",
    "tp2",
    "tp3",
    "risk_reward",
    "expires_at",
    "invalidation",
    "features",
    "reason_codes",
    "risk_flags",
    "data_quality",
    "is_simulated",
)


def _normalise(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value:.8f}"
    if isinstance(value, float):
        return f"{Decimal(str(value)):.8f}"
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def canonical_payload(signal: dict[str, Any]) -> str:
    """Deterministic JSON of the hashed subset of a signal."""
    subset = {k: _normalise(signal.get(k)) for k in HASH_FIELDS}
    return json.dumps(subset, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def compute_row_hash(signal: dict[str, Any], prev_hash: str) -> str:
    return sha256_hex(canonical_payload(signal) + prev_hash)


def verify_row(signal: dict[str, Any], prev_hash: str, claimed_hash: str) -> bool:
    return compute_row_hash(signal, prev_hash) == claimed_hash


def verify_chain(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Walk an ordered list of signal rows and report the first break, if any.

    ``rows`` must be ordered by ``seq`` ascending and contain ``prev_hash`` and
    ``row_hash`` plus the hashed payload fields.
    """
    prev = GENESIS
    checked = 0
    for row in rows:
        if row["prev_hash"] != prev:
            return {
                "ok": False,
                "checked": checked,
                "broken_at": str(row["signal_id"]),
                "error": "prev_hash does not match previous row_hash",
                "expected_prev": prev,
                "found_prev": row["prev_hash"],
            }
        recomputed = compute_row_hash(row, prev)
        if recomputed != row["row_hash"]:
            return {
                "ok": False,
                "checked": checked,
                "broken_at": str(row["signal_id"]),
                "error": "row_hash does not match recomputed payload hash",
                "expected_hash": recomputed,
                "found_hash": row["row_hash"],
            }
        prev = row["row_hash"]
        checked += 1
    return {"ok": True, "checked": checked, "head": prev, "broken_at": None}


# ───────────────────────────── Merkle tree ──────────────────────────────────


def merkle_root(leaves: list[str]) -> str:
    """Root over hex leaf hashes. Odd nodes are promoted, not duplicated."""
    if not leaves:
        return GENESIS
    level = list(leaves)
    while len(level) > 1:
        nxt: list[str] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(sha256_hex(level[i] + level[i + 1]))
        if len(level) % 2 == 1:
            nxt.append(level[-1])
        level = nxt
    return level[0]


def merkle_proof(leaves: list[str], index: int) -> list[dict[str, str]]:
    """Sibling path proving ``leaves[index]`` is in the tree.

    A promoted odd node has no sibling at that level, so it contributes no proof
    step — it simply carries upward.
    """
    if index < 0 or index >= len(leaves):
        raise IndexError("leaf index out of range")
    proof: list[dict[str, str]] = []
    level = list(leaves)
    idx = index

    while len(level) > 1:
        pairs = len(level) // 2
        nxt = [sha256_hex(level[2 * i] + level[2 * i + 1]) for i in range(pairs)]
        promoted = len(level) % 2 == 1
        if promoted:
            nxt.append(level[-1])

        if idx < pairs * 2:
            if idx % 2 == 0:
                proof.append({"position": "right", "hash": level[idx + 1]})
            else:
                proof.append({"position": "left", "hash": level[idx - 1]})
            idx //= 2
        else:
            idx = pairs           # this node was promoted untouched
        level = nxt
    return proof


def verify_merkle_proof(leaf: str, proof: list[dict[str, str]], root: str) -> bool:
    node = leaf
    for step in proof:
        node = (
            sha256_hex(step["hash"] + node)
            if step["position"] == "left"
            else sha256_hex(node + step["hash"])
        )
    return node == root
