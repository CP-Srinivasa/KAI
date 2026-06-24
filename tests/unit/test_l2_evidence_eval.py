"""Unit tests for the L2 evidence evaluation core (Sprint 2, B-003).

The crux of B-003: the fee/mempool series is slow and HIGHLY autocorrelated, so a
naive IID bootstrap / hit-rate would manufacture significance. We test the
autocorrelation-robust **moving-block bootstrap** and the look-ahead-safe
**point-in-time join** that pairs each measurement with a STRICTLY-later outcome.
"""

from __future__ import annotations

from app.observability.l2_evidence_eval import (
    evaluate_feature_direction,
    moving_block_bootstrap_p_mean_positive,
    pit_join,
)

# --- moving-block bootstrap ------------------------------------------------------


def test_block_bootstrap_below_min_sample_is_none() -> None:
    assert moving_block_bootstrap_p_mean_positive([1.0, 2.0], min_sample=8) is None


def test_block_bootstrap_all_positive_near_one() -> None:
    p = moving_block_bootstrap_p_mean_positive([1.0] * 30, min_sample=8, seed=7)
    assert p is not None and p > 0.99


def test_block_bootstrap_all_negative_near_zero() -> None:
    p = moving_block_bootstrap_p_mean_positive([-1.0] * 30, min_sample=8, seed=7)
    assert p is not None and p < 0.01


def test_block_bootstrap_deterministic_with_seed() -> None:
    vals = [0.3, -0.1, 0.2, -0.4, 0.5, 0.1, -0.2, 0.3, 0.0, 0.6, -0.3, 0.2]
    a = moving_block_bootstrap_p_mean_positive(vals, seed=42)
    b = moving_block_bootstrap_p_mean_positive(vals, seed=42)
    assert a == b and a is not None and 0.0 <= a <= 1.0


# --- point-in-time join ----------------------------------------------------------


def test_pit_join_pairs_with_strictly_later_outcome() -> None:
    measurements = [
        {"ts": "2026-06-01T00:00:00+00:00", "symbol": "BTC/USDT", "fee_percentile": 0.9},
    ]
    outcomes = [
        # look-ahead (BEFORE measurement) — must NOT be used
        {"symbol": "BTC/USDT", "entry_ts": "2026-05-31T23:59:00+00:00", "net_bps": 99.0},
        # valid: at/after measurement
        {"symbol": "BTC/USDT", "entry_ts": "2026-06-01T00:05:00+00:00", "net_bps": 12.0},
        {"symbol": "BTC/USDT", "entry_ts": "2026-06-01T01:00:00+00:00", "net_bps": 50.0},
    ]
    pairs = pit_join(measurements, outcomes)
    assert len(pairs) == 1
    assert pairs[0][1]["net_bps"] == 12.0  # earliest qualifying (no look-ahead)


def test_pit_join_skips_other_symbols_and_unmatched() -> None:
    measurements = [
        {"ts": "2026-06-01T00:00:00+00:00", "symbol": "ETH/USDT", "fee_percentile": 0.2},
        {"ts": "2026-06-02T00:00:00+00:00", "symbol": "BTC/USDT", "fee_percentile": 0.8},
    ]
    outcomes = [
        {"symbol": "BTC/USDT", "entry_ts": "2026-06-01T00:05:00+00:00", "net_bps": 10.0},
    ]
    pairs = pit_join(measurements, outcomes)
    # ETH measurement: no ETH outcome → unmatched; BTC measurement at 06-02 has no
    # later BTC outcome (only 06-01) → unmatched. Result empty.
    assert pairs == []


# --- direction learning ----------------------------------------------------------


def test_evaluate_feature_direction_detects_split(monkeypatch) -> None:
    # High-fee measurements → negative outcomes; low-fee → positive. A real,
    # learnable contrarian direction on the fee feature.
    pairs = []
    for i in range(20):
        pairs.append(({"fee_percentile": 0.9}, {"net_bps": -20.0 - i}))
        pairs.append(({"fee_percentile": 0.1}, {"net_bps": 20.0 + i}))
    result = evaluate_feature_direction(pairs, feature_key="fee_percentile", min_sample=8)
    assert result["n_high"] == 20 and result["n_low"] == 20
    # high-fee group mean is negative, low-fee positive → spread is meaningful
    assert result["mean_high"] < 0 < result["mean_low"]
    assert result["direction"] in {"contrarian", "pro_trend", "inconclusive"}
    assert result["direction"] == "contrarian"  # high feature → adverse → fade it


def test_evaluate_feature_direction_insufficient_is_honest() -> None:
    pairs = [({"fee_percentile": 0.9}, {"net_bps": -1.0})]
    result = evaluate_feature_direction(pairs, feature_key="fee_percentile", min_sample=8)
    assert result["direction"] == "insufficient"
