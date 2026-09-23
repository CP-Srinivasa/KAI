"""Unit tests for the L2 evidence evaluation core (Sprint 2, B-003).

The crux of B-003: the fee/mempool series is slow and HIGHLY autocorrelated, so a
naive IID bootstrap / hit-rate would manufacture significance. We test the
autocorrelation-robust **moving-block bootstrap** and the look-ahead-safe
**point-in-time join** that pairs each uniquely identified measurement with its
direction-compatible, bounded-age outcome.
"""

from __future__ import annotations

import pytest

from app.observability.l2_evidence_eval import (
    evaluate_feature_direction,
    moving_block_bootstrap_p_mean_positive,
    pit_join,
)

# --- moving-block bootstrap ------------------------------------------------------


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), True])
def test_bootstrap_rejects_invalid_values_instead_of_negative_evidence(invalid) -> None:
    with pytest.raises(ValueError, match="finite numbers"):
        moving_block_bootstrap_p_mean_positive([invalid] * 8)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), True, "bad"])
@pytest.mark.parametrize("field", ["feature", "outcome"])
def test_invalid_values_cannot_manufacture_direction(invalid, field) -> None:
    high = ({"fee_percentile": 0.9}, {"net_bps": -20.0})
    low = ({"fee_percentile": 0.1}, {"net_bps": 20.0})
    if field == "feature":
        low[0]["fee_percentile"] = invalid
    else:
        high[1]["net_bps"] = invalid
    result = evaluate_feature_direction([high] * 8 + [low] * 8, feature_key="fee_percentile")
    assert result["direction"] == "insufficient"
    assert result["n_high"] + result["n_low"] == 8
    assert result[f"n_invalid_{field}"] == 8


def test_missing_feature_is_not_imputed_as_low_evidence() -> None:
    pairs = [({"fee_percentile": 0.9}, {"net_bps": -20.0})] * 8
    pairs += [({}, {"net_bps": 20.0})] * 8
    result = evaluate_feature_direction(pairs, feature_key="fee_percentile")
    assert result["direction"] == "insufficient"
    assert result["n_low"] == 0
    assert result["n_null_feature"] == 8


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
        {
            "candidate_id": "valid",
            "ts": "2026-06-01T00:00:00+00:00",
            "symbol": "BTC/USDT",
            "direction": "long",
            "fee_percentile": 0.9,
        },
        {
            "candidate_id": "before",
            "ts": "2026-06-01T00:00:00+00:00",
            "symbol": "BTC/USDT",
            "direction": "long",
            "fee_percentile": 0.1,
        },
    ]
    outcomes = [
        {
            "candidate_id": "before",
            "symbol": "BTC/USDT",
            "side": "long",
            "entry_ts": "2026-05-31T23:59:00+00:00",
            "net_bps": 99.0,
        },
        {
            "candidate_id": "valid",
            "symbol": "BTC/USDT",
            "side": "long",
            "entry_ts": "2026-06-01T00:05:00+00:00",
            "net_bps": 12.0,
        },
    ]
    pairs = pit_join(measurements, outcomes)
    assert len(pairs) == 1
    assert pairs[0][1]["net_bps"] == 12.0


def test_pit_join_skips_other_symbols_and_unmatched() -> None:
    measurements = [
        {
            "candidate_id": "c1",
            "ts": "2026-06-01T00:00:00+00:00",
            "symbol": "ETH/USDT",
            "direction": "long",
            "fee_percentile": 0.2,
        },
        {
            "candidate_id": "c2",
            "ts": "2026-06-02T00:00:00+00:00",
            "symbol": "BTC/USDT",
            "direction": "long",
            "fee_percentile": 0.8,
        },
    ]
    outcomes = [
        {
            "candidate_id": "c1",
            "symbol": "BTC/USDT",
            "side": "long",
            "entry_ts": "2026-06-01T00:05:00+00:00",
            "net_bps": 10.0,
        },
    ]
    pairs = pit_join(measurements, outcomes)
    assert pairs == []


def test_pit_join_does_not_duplicate_an_outcome_for_repeated_measurements() -> None:
    measurements = [
        {
            "candidate_id": "same-candidate",
            "ts": f"2026-06-01T00:0{minute}:00+00:00",
            "symbol": "BTC/USDT",
            "direction": "long",
        }
        for minute in (0, 1)
    ]
    outcomes = [
        {
            "candidate_id": "same-candidate",
            "symbol": "BTC/USDT",
            "side": "long",
            "entry_ts": "2026-06-01T00:02:00+00:00",
            "net_bps": 10.0,
        }
    ]
    assert pit_join(measurements, outcomes) == []


def test_pit_join_rejects_duplicate_outcome_identity() -> None:
    measurements = [
        {
            "candidate_id": "same-candidate",
            "ts": "2026-06-01T00:00:00+00:00",
            "symbol": "BTC/USDT",
            "direction": "long",
        }
    ]
    outcomes = [
        {
            "candidate_id": "same-candidate",
            "symbol": "BTC/USDT",
            "side": "long",
            "entry_ts": "2026-06-01T00:01:00+00:00",
            "net_bps": net_bps,
        }
        for net_bps in (10.0, 20.0)
    ]
    assert pit_join(measurements, outcomes) == []


def test_pit_join_rejects_wrong_direction_and_unrelated_candidate() -> None:
    measurements = [
        {
            "candidate_id": "wanted",
            "ts": "2026-06-01T00:00:00+00:00",
            "symbol": "BTC/USDT",
            "direction": "long",
        }
    ]
    outcomes = [
        {
            "candidate_id": "wanted",
            "symbol": "BTC/USDT",
            "side": "short",
            "entry_ts": "2026-06-01T00:01:00+00:00",
            "net_bps": -10.0,
        },
        {
            "candidate_id": "other",
            "symbol": "BTC/USDT",
            "side": "long",
            "entry_ts": "2026-06-01T00:00:30+00:00",
            "net_bps": 99.0,
        },
    ]
    assert pit_join(measurements, outcomes) == []


def test_pit_join_enforces_maximum_age() -> None:
    measurements = [
        {
            "candidate_id": "too-old",
            "ts": "2026-06-01T00:00:00+00:00",
            "symbol": "BTC/USDT",
            "direction": "long",
        }
    ]
    outcomes = [
        {
            "candidate_id": "too-old",
            "symbol": "BTC/USDT",
            "side": "long",
            "entry_ts": "2026-06-01T00:05:01+00:00",
            "net_bps": 10.0,
        }
    ]
    assert pit_join(measurements, outcomes) == []
    assert len(pit_join(measurements, outcomes, max_age_seconds=301.0)) == 1


def test_pit_join_accepts_merged_producer_context_without_backdating() -> None:
    measurement = {
        "candidate_id": "cycle-42",
        "symbol": "BTC/USDT",
        "direction": "long",
        "ts": "2026-09-23T10:00:02+00:00",
        "decision_ts": "2026-09-23T10:00:00+00:00",
        "reference_price_ts": "2026-09-23T09:59:59+00:00",
        "causality_ok": True,
    }
    outcome = {
        "candidate_id": "cycle-42",
        "symbol": "BTC/USDT",
        "side": "long",
        "entry_ts": "2026-09-23T10:00:00+00:00",
        "net_bps": 4.0,
    }

    assert pit_join([measurement], [outcome]) == [(measurement, outcome)]


@pytest.mark.parametrize(
    ("override", "outcome_ts"),
    [
        ({"causality_ok": False}, "2026-09-23T10:00:00+00:00"),
        ({"causality_ok": None}, "2026-09-23T10:00:00+00:00"),
        ({"reference_price_ts": "2026-09-23T10:00:01+00:00"}, "2026-09-23T10:00:00+00:00"),
        ({}, "2026-09-23T10:00:01+00:00"),
        ({"ts": "2026-09-23T09:59:59+00:00"}, "2026-09-23T10:00:00+00:00"),
    ],
)
def test_pit_join_rejects_invalid_merged_producer_context(override, outcome_ts) -> None:
    measurement = {
        "candidate_id": "cycle-42",
        "symbol": "BTC/USDT",
        "direction": "long",
        "ts": "2026-09-23T10:00:02+00:00",
        "decision_ts": "2026-09-23T10:00:00+00:00",
        "reference_price_ts": "2026-09-23T09:59:59+00:00",
        "causality_ok": True,
        **override,
    }
    outcome = {
        "candidate_id": "cycle-42",
        "symbol": "BTC/USDT",
        "side": "long",
        "entry_ts": outcome_ts,
        "net_bps": 4.0,
    }

    assert pit_join([measurement], [outcome]) == []


def test_pit_join_missing_provenance_and_invalid_timestamps_are_unmatched() -> None:
    complete_outcome = {
        "candidate_id": "c1",
        "symbol": "BTC/USDT",
        "side": "long",
        "entry_ts": "2026-06-01T00:01:00+00:00",
        "net_bps": 10.0,
    }
    measurements = [
        {
            "ts": "2026-06-01T00:00:00+00:00",
            "symbol": "BTC/USDT",
            "direction": "long",
        },
        {
            "candidate_id": "c1",
            "ts": "not-a-time",
            "symbol": "BTC/USDT",
            "direction": "long",
        },
    ]
    assert pit_join(measurements, [complete_outcome]) == []


def test_pit_join_rejects_an_unbounded_or_invalid_age() -> None:
    for invalid in (0.0, -1.0, float("inf"), float("nan")):
        try:
            pit_join([], [], max_age_seconds=invalid)
        except ValueError as exc:
            assert "max_age_seconds" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {invalid!r}")


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


def test_evaluate_feature_direction_tolerates_null_feature_values() -> None:
    # Producer records "source unavailable" as explicit null (live since 2026-07-01:
    # fee_percentile null in l2_evidence_shadow.jsonl). Must exclude + count, not crash.
    pairs = [
        ({"fee_percentile": None}, {"net_bps": 5.0}),
        ({"fee_percentile": 0.9}, {"net_bps": -10.0}),
        ({"fee_percentile": 0.1}, {"net_bps": 10.0}),
    ]
    result = evaluate_feature_direction(pairs, feature_key="fee_percentile", min_sample=8)
    assert result["n_high"] == 1 and result["n_low"] == 1
    assert result["n_null_feature"] == 1
    assert result["direction"] == "insufficient"
