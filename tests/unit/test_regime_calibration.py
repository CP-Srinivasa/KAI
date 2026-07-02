"""Unit tests for regime-specific calibrator bundles."""

from __future__ import annotations

import random

import pytest

from app.learning.calibration import OutcomePair, compute_calibration
from app.learning.regime_calibration import (
    GLOBAL_BUCKET,
    RegimeCalibratorBundle,
    RegimeCalibratorEntry,
    fit_regime_calibrators,
)

# --------------------------------------------------------------------- helpers


def _overconfident_in(
    n: int,
    *,
    regime: str,
    seed: int = 1,
    win_rate: float = 0.5,
    p_low: float = 0.70,
    p_high: float = 0.95,
) -> list[OutcomePair]:
    rng = random.Random(seed)
    return [
        OutcomePair(
            decision_id=f"{regime}_{i}",
            predicted_probability=rng.uniform(p_low, p_high),
            actual_outcome=1 if rng.random() < win_rate else 0,
            regime=regime,
        )
        for i in range(n)
    ]


# ============================================================================
# Bucketing + sparse fallback
# ============================================================================


def test_each_regime_with_enough_data_gets_its_own_calibrator():
    pairs = _overconfident_in(80, regime="low_vol", seed=1, win_rate=0.85) + _overconfident_in(
        80, regime="high_vol", seed=2, win_rate=0.40
    )
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    assert "low_vol" in bundle.regimes
    assert "high_vol" in bundle.regimes
    assert not bundle.regimes["low_vol"].is_fallback
    assert not bundle.regimes["high_vol"].is_fallback
    # The high_vol regime is much more overconfident → its slope should be
    # smaller (= heavier squashing) than low_vol's.
    assert bundle.regimes["high_vol"].slope < bundle.regimes["low_vol"].slope, (
        "high_vol calibrator should compress more than low_vol"
    )


def test_sparse_regime_falls_back_to_global():
    pairs = (
        _overconfident_in(80, regime="normal", seed=3, win_rate=0.50)
        + _overconfident_in(5, regime="crisis", seed=4)  # too few to fit
    )
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    assert "crisis" in bundle.regimes
    crisis = bundle.regimes["crisis"]
    assert crisis.is_fallback
    # Fallback entry mirrors global numbers
    assert crisis.intercept == bundle.global_calibrator.intercept
    assert crisis.slope == bundle.global_calibrator.slope


def test_expected_regimes_are_emitted_even_if_empty():
    pairs = _overconfident_in(80, regime="normal", seed=5)
    bundle = fit_regime_calibrators(
        pairs,
        min_pairs_per_regime=30,
        expected_regimes=("low_vol", "normal", "elevated", "high_vol", "crisis"),
    )
    assert set(bundle.regimes) == {"low_vol", "normal", "elevated", "high_vol", "crisis"}
    # Only 'normal' has data → others are fallback
    for regime in ("low_vol", "elevated", "high_vol", "crisis"):
        assert bundle.regimes[regime].is_fallback


def test_pairs_without_regime_only_feed_global_calibrator():
    """A regime=None pair should not create a regime bucket, but should still
    contribute to the global fit."""
    rng = random.Random(7)
    # Vary predictions across [0.70, 0.95] with systematic overconfidence
    # (~50 % win rate) so the OLS fit has signal to learn from.
    pairs = [
        OutcomePair(
            decision_id=f"d_{i}",
            predicted_probability=rng.uniform(0.70, 0.95),
            actual_outcome=1 if rng.random() < 0.5 else 0,
            regime=None,
        )
        for i in range(120)
    ]
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    assert bundle.regimes == {}
    # Global learned to lower confidence
    assert not bundle.global_calibrator.is_identity
    assert bundle.global_calibrator.slope < 1.0 or bundle.global_calibrator.intercept < 0


# ============================================================================
# Apply / transform
# ============================================================================


def test_transform_uses_regime_specific_calibrator():
    pairs = _overconfident_in(80, regime="low_vol", seed=11, win_rate=0.95) + _overconfident_in(
        80, regime="high_vol", seed=12, win_rate=0.20
    )
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    # 0.85 raw posterior in low_vol (well calibrated, win rate 0.95)
    # vs. high_vol (win rate 0.20 → systematically overconfident)
    p_low = bundle.transform(0.85, regime="low_vol")
    p_high = bundle.transform(0.85, regime="high_vol")
    assert p_high < p_low, "high_vol must squash more aggressively than low_vol"


def test_transform_unknown_regime_falls_back_to_global():
    pairs = _overconfident_in(80, regime="normal", seed=21)
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    p_unknown = bundle.transform(0.85, regime="never_seen_regime")
    p_global = bundle.transform(0.85, regime=None)
    assert p_unknown == p_global


def test_transform_none_regime_uses_global():
    pairs = _overconfident_in(80, regime="normal", seed=22)
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    p_none = bundle.transform(0.80)
    p_explicit_global = bundle.transform(0.80, regime=None)
    assert p_none == p_explicit_global


def test_sparse_regime_transform_equals_global():
    """Even when a sparse regime is named explicitly, transform routes to
    global because the entry's is_fallback flag is honored."""
    pairs = _overconfident_in(80, regime="normal", seed=31) + _overconfident_in(
        5, regime="crisis", seed=32
    )
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    assert bundle.regimes["crisis"].is_fallback
    p_crisis = bundle.transform(0.85, regime="crisis")
    p_global = bundle.transform(0.85, regime=None)
    assert p_crisis == p_global


def test_identity_bundle_is_a_noop():
    """If the data isn't overconfident at all, the global fit returns
    Identity; transform must be a no-op (modulo eps clamping)."""
    rng = random.Random(99)
    # Well-calibrated data: P(win) ≈ predicted_probability
    pairs: list[OutcomePair] = []
    for i in range(200):
        p = rng.uniform(0.10, 0.90)
        pairs.append(
            OutcomePair(
                decision_id=f"d_{i}",
                predicted_probability=p,
                actual_outcome=1 if rng.random() < p else 0,
                regime="normal",
            )
        )
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    # Either identity or close to identity; both → transform near identity
    for raw in (0.10, 0.40, 0.60, 0.85):
        out = bundle.transform(raw, regime="normal")
        assert abs(out - raw) < 0.10, f"raw={raw} got={out}"


# ============================================================================
# Persistence / round-trip via parameter_set
# ============================================================================


def test_to_parameter_set_round_trips_through_from_parameter_set():
    pairs = (
        _overconfident_in(60, regime="low_vol", seed=41)
        + _overconfident_in(60, regime="high_vol", seed=42, win_rate=0.30)
        + _overconfident_in(8, regime="crisis", seed=43)
    )
    bundle = fit_regime_calibrators(
        pairs,
        min_pairs_per_regime=30,
        expected_regimes=("low_vol", "high_vol", "crisis"),
    )
    payload = bundle.to_parameter_set()

    # Schema sanity
    assert payload["min_pairs_per_regime"] == 30
    assert "global" in payload
    assert "regimes" in payload
    assert set(payload["regimes"]) == {"low_vol", "high_vol", "crisis"}

    # Round-trip
    rebuilt = RegimeCalibratorBundle.from_parameter_set(payload)
    assert rebuilt.global_calibrator == bundle.global_calibrator
    assert rebuilt.regimes == bundle.regimes
    assert rebuilt.min_pairs_per_regime == bundle.min_pairs_per_regime

    # Apply identity check
    for regime in (None, "low_vol", "high_vol", "crisis", "unknown"):
        assert rebuilt.transform(0.80, regime=regime) == bundle.transform(0.80, regime=regime)


def test_from_parameter_set_rejects_malformed_payload():
    with pytest.raises(ValueError, match="must be a dict"):
        RegimeCalibratorBundle.from_parameter_set("not a dict")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must contain"):
        RegimeCalibratorBundle.from_parameter_set({"regimes": {}})
    with pytest.raises(ValueError, match="'regimes' must be a dict"):
        RegimeCalibratorBundle.from_parameter_set(
            {
                "global": {
                    "intercept": 0,
                    "slope": 1,
                    "n_fitted": 0,
                    "is_identity": True,
                    "is_fallback": False,
                },
                "regimes": "not a dict",
            }
        )


def test_global_bucket_constant_is_used_in_serialized_form():
    """Persistence must use a stable label for the global calibrator so that
    audit diffs across versions stay readable."""
    pairs = _overconfident_in(80, regime="normal", seed=51)
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    payload = bundle.to_parameter_set()
    assert payload["global"]["regime"] == GLOBAL_BUCKET


# ============================================================================
# Integration with downstream calibration metrics
# ============================================================================


def test_per_regime_brier_after_apply_is_no_worse_than_identity_for_dominant_regimes():
    """For a regime with enough pairs and obvious overconfidence, applying the
    fitted regime calibrator should yield Brier ≤ identity-Brier on those pairs."""
    pairs = _overconfident_in(200, regime="high_vol", seed=61, win_rate=0.40)
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    before = compute_calibration(pairs)
    corrected = [
        OutcomePair(
            decision_id=p.decision_id,
            predicted_probability=bundle.transform(p.predicted_probability, regime="high_vol"),
            actual_outcome=p.actual_outcome,
            weight=p.weight,
            timestamp_utc=p.timestamp_utc,
            regime=p.regime,
        )
        for p in pairs
    ]
    after = compute_calibration(corrected)
    assert before.brier_score is not None
    assert after.brier_score is not None
    assert after.brier_score <= before.brier_score + 1e-6


# ============================================================================
# Edge cases
# ============================================================================


def test_empty_pairs_yields_identity_bundle():
    bundle = fit_regime_calibrators([], min_pairs_per_regime=30)
    assert bundle.regimes == {}
    assert bundle.global_calibrator.is_identity


def test_single_regime_pairs_only_emits_that_regime_unless_expected():
    pairs = _overconfident_in(80, regime="normal", seed=71)
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    assert set(bundle.regimes) == {"normal"}


def test_entry_dataclass_is_frozen():
    entry = RegimeCalibratorEntry(
        regime="low_vol",
        intercept=0.05,
        slope=0.92,
        n_fitted=80,
        is_identity=False,
        is_fallback=False,
    )
    # Pydantic v2 raises ValidationError for frozen-model assignment
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        entry.intercept = 0.99  # type: ignore[misc]
