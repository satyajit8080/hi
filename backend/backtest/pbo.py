"""Probability of Backtest Overfitting via combinatorially symmetric cross-validation.

Bailey, Borwein, López de Prado & Zhu (2017). Given a matrix of per-period
returns for N parameter configurations, split the periods into S blocks, form
every (or a sampled subset of) train/test partition of S/2 blocks each, pick the
configuration that is best in-sample, and record its out-of-sample rank. PBO is
the fraction of partitions where the in-sample winner lands in the bottom half
out of sample. Above ~0.5 means the selection process is picking noise.
"""
from __future__ import annotations

import itertools
import math

import numpy as np


def _sharpe(x: np.ndarray) -> float:
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / sd) if sd > 0 else 0.0


def run(returns_matrix: np.ndarray, n_blocks: int = 16, max_combinations: int = 200, seed: int = 7) -> dict:
    """`returns_matrix` is periods x configurations."""
    m = np.asarray(returns_matrix, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2 or m.shape[0] < n_blocks * 2:
        return {"sufficient": False, "note": "Need at least 2 configurations and 2 periods per block."}

    periods, n_cfg = m.shape
    blocks = np.array_split(np.arange(periods), n_blocks)
    half = n_blocks // 2
    all_combos = list(itertools.combinations(range(n_blocks), half))
    rng = np.random.default_rng(seed)
    if len(all_combos) > max_combinations:
        idx = rng.choice(len(all_combos), size=max_combinations, replace=False)
        combos = [all_combos[i] for i in idx]
    else:
        combos = all_combos

    logits = []
    for train_blocks in combos:
        test_blocks = [b for b in range(n_blocks) if b not in train_blocks]
        tr = np.concatenate([blocks[b] for b in train_blocks])
        te = np.concatenate([blocks[b] for b in test_blocks])
        is_perf = np.array([_sharpe(m[tr, c]) for c in range(n_cfg)])
        oos_perf = np.array([_sharpe(m[te, c]) for c in range(n_cfg)])
        best = int(np.argmax(is_perf))
        # Relative OOS rank of the in-sample winner, in (0, 1).
        rank = (np.sum(oos_perf < oos_perf[best]) + 0.5) / n_cfg
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1 - rank)))

    logits_arr = np.array(logits)
    return {
        "sufficient": True,
        "configurations": int(n_cfg),
        "partitions": len(combos),
        "pbo": round(float((logits_arr <= 0).mean()), 4),
        "mean_logit": round(float(logits_arr.mean()), 4),
        "interpretation": (
            "PBO is the probability that the configuration chosen for its in-sample performance is "
            "below median out of sample. Values near 0.5 or above mean the parameter search is "
            "selecting noise."
        ),
    }
