"""Unit tests for walk-forward calibration validation."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.learning.calibration import OutcomePair
from app.learning.walk_forward import (
    DEFAULT_MIN_BRIER_IMPROVEMENT,
    WalkForwardConfig,
    WalkForwardReport,
    walk_forward_validate,
)

# --------------------------------------------------------------------- helpers


def _overconfident_pairs(n: int = 200, *, seed: int = 1) -> list[OutcomePair]:
    """Predicted probabilities high (0.7..0.95), but true win rate ≈ 0.5.

    Calibration should learn slope < 1 + intercept < 0 → squashes predictions
    toward the observed mean → OoS Brier improves substantially.
    """
    rng = random.Random(seed)
    pairs: list[OutcomePair] = []
    for i in range(n):
        p_pred = rng.uniform(0.70, 0.95)
        actual = 1 if rng.random() < 0.5 else 0
        pairs.append(
            OutcomePair(
                decision_id=f"d_{i}",
                predicted_probability=p_pred,
                actual_outcome=actual,
            )
        )
    return pairs


def _well_calibrated_pairs(n: int = 200, *, seed: int = 2) -> list[OutcomePair]:
    """For each predicted probability p, the actual win rate is ≈ p.

    Identity calibrator should win — proposed adjustments shouldn't beat the
    Brier improvement threshold.
    """
    rng = random.Random(seed)
    pairs: list[OutcomePair] = []
    for i in range(n):
        p_pred = rng.uniform(0.05, 0.95)
        actual = 1 if rng.random() < p_pred else 0
        pairs.append(
            OutcomePair(
                decision_id=f"d_{i}",
                predicted_probability=p_pred,
                actual_outcome=actual,
            )
        )
    return pairs


def _with_timestamps(
    pairs: list[OutcomePair], *, start_minute_offset: int = 0
) -> list[OutcomePair]:
    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    return [
        OutcomePair(
            decision_id=p.decision_id,
            predicted_probability=p.predicted_probability,
            actual_outcome=p.actual_outcome,
            weight=p.weight,
            timestamp_utc=base + timedelta(minutes=start_minute_offset + i),
        )
        for i, p in enumerate(pairs)
    ]


# ============================================================================
# Decision: approve / reject / insufficient_data
# ============================================================================


def test_overconfident_engine_is_approved():
    pairs = _overconfident_pairs(n=300, seed=11)
    report = walk_forward_validate(pairs)
    assert isinstance(report, WalkForwardReport)
    assert report.decision == "approve", report.decision_reasons
    assert report.mean_oos_brier_improvement >= DEFAULT_MIN_BRIER_IMPROVEMENT
    assert report.consistency_ratio >= 0.6
    # Some splits saw a non-identity calibrator
    assert any(not s.calibrator_is_identity for s in report.splits)


def test_well_calibrated_engine_is_rejected():
    pairs = _well_calibrated_pairs(n=300, seed=21)
    report = walk_forward_validate(pairs)
    assert report.decision == "reject", report.decision_reasons
    assert any(
        "mean OoS Brier improvement" in r or "consistency" in r for r in report.decision_reasons
    )


def test_insufficient_data_returns_dedicated_decision():
    pairs = _overconfident_pairs(n=20, seed=31)
    report = walk_forward_validate(pairs)
    assert report.decision == "insufficient_data"
    assert report.n_splits_run == 0
    assert any("need >=" in r for r in report.decision_reasons)


def test_empty_input_returns_insufficient_data():
    report = walk_forward_validate([])
    assert report.decision == "insufficient_data"
    assert report.n_pairs == 0
    assert report.splits == ()


# ============================================================================
# Splits + chronological order
# ============================================================================


def test_split_count_respected_for_sufficient_data():
    cfg = WalkForwardConfig(n_splits=4, train_fraction=0.50, min_test_size=20)
    pairs = _overconfident_pairs(n=400, seed=12)
    report = walk_forward_validate(pairs, config=cfg)
    assert report.n_splits_run == 4
    assert report.config.n_splits == 4


def test_train_grows_monotonically_across_folds():
    pairs = _overconfident_pairs(n=400, seed=13)
    report = walk_forward_validate(pairs)
    sizes = [s.n_train for s in report.splits]
    assert sizes == sorted(sizes)
    assert sizes[0] < sizes[-1]


def test_pairs_are_sorted_by_timestamp_when_present():
    """Validator sorts on timestamp when all pairs carry one."""
    base_pairs = _overconfident_pairs(n=200, seed=14)
    timestamped = _with_timestamps(base_pairs)
    # Shuffle and re-validate — must produce identical fold structure
    rng = random.Random(99)
    shuffled = list(timestamped)
    rng.shuffle(shuffled)
    a = walk_forward_validate(timestamped)
    b = walk_forward_validate(shuffled)
    assert [s.n_train for s in a.splits] == [s.n_train for s in b.splits]
    assert [s.n_test for s in a.splits] == [s.n_test for s in b.splits]


def test_input_order_preserved_when_no_timestamps():
    """Without timestamps, the validator trusts the caller's ordering."""
    pairs = _overconfident_pairs(n=200, seed=15)
    # Reverse order — train_min_idx pairs have wildly different distribution
    # → splits will differ from the originally-ordered version.
    a = walk_forward_validate(pairs)
    b = walk_forward_validate(list(reversed(pairs)))
    # They produce different fold sizes only if the reversal changed anything
    # we can detect; minimum check: both reports are produced without error
    # and respect their own input.
    assert a.n_pairs == b.n_pairs
    # Calibrator parameters typically differ between forward and reversed
    forward_intercepts = [s.calibrator_intercept for s in a.splits]
    reverse_intercepts = [s.calibrator_intercept for s in b.splits]
    assert forward_intercepts != reverse_intercepts


# ============================================================================
# Threshold tuning
# ============================================================================


def test_strict_threshold_can_flip_approve_to_reject():
    """A high min_brier_improvement threshold should reject borderline wins."""
    pairs = _overconfident_pairs(n=300, seed=16)
    base = walk_forward_validate(pairs)
    assert base.decision == "approve"
    strict = walk_forward_validate(pairs, config=WalkForwardConfig(min_brier_improvement=0.20))
    assert strict.decision == "reject"
    assert any("mean OoS Brier improvement" in r for r in strict.decision_reasons)


def test_strict_consistency_can_flip_to_reject():
    pairs = _overconfident_pairs(n=300, seed=17)
    strict = walk_forward_validate(
        pairs,
        config=WalkForwardConfig(min_consistency=1.0),  # all folds must improve
    )
    # With small bucket sizes, any noise can flip a fold — strict 1.0 likely rejects
    if any(not s.improved for s in strict.splits):
        assert strict.decision == "reject"


def test_decision_reasons_are_populated_on_both_outcomes():
    pairs = _overconfident_pairs(n=300, seed=18)
    approve = walk_forward_validate(pairs)
    assert approve.decision_reasons  # never empty
    reject = walk_forward_validate(_well_calibrated_pairs(n=300, seed=19))
    assert reject.decision_reasons


# ============================================================================
# Output structure
# ============================================================================


def test_report_round_trips_through_pydantic():
    pairs = _overconfident_pairs(n=200, seed=20)
    report = walk_forward_validate(pairs)
    payload = report.model_dump(mode="json")
    rebuilt = WalkForwardReport.model_validate(payload)
    assert rebuilt.decision == report.decision
    assert len(rebuilt.splits) == len(report.splits)
    assert rebuilt.mean_oos_brier_improvement == report.mean_oos_brier_improvement


def test_split_records_are_self_describing():
    pairs = _overconfident_pairs(n=200, seed=22)
    report = walk_forward_validate(pairs)
    for split in report.splits:
        # Each split records both before/after metrics + the calibrator used
        assert split.n_train > 0
        assert split.n_test > 0
        assert split.brier_test_before >= 0.0
        assert split.brier_test_after >= 0.0
        assert split.brier_improvement == pytest.approx(
            split.brier_test_before - split.brier_test_after, abs=1e-6
        )


def test_config_is_reflected_in_report():
    cfg = WalkForwardConfig(
        n_splits=3,
        train_fraction=0.60,
        min_brier_improvement=0.01,
        min_consistency=0.5,
    )
    pairs = _overconfident_pairs(n=300, seed=23)
    report = walk_forward_validate(pairs, config=cfg)
    assert report.config == cfg


# ============================================================================
# Edge cases
# ============================================================================


def test_degenerate_constant_predictions_does_not_crash():
    """All p_pred == 0.5 → fit returns IdentityCalibrator → no improvement."""
    rng = random.Random(42)
    pairs = [
        OutcomePair(
            decision_id=f"d_{i}",
            predicted_probability=0.5,
            actual_outcome=1 if rng.random() < 0.5 else 0,
        )
        for i in range(200)
    ]
    report = walk_forward_validate(pairs)
    assert report.decision in ("reject", "approve", "insufficient_data")
    # No exception, no NaN: every split's identity flag should be True
    if report.splits:
        assert all(s.calibrator_is_identity for s in report.splits)


def test_constant_outcome_does_not_crash():
    """All actual_outcome == 1 → fit returns IdentityCalibrator (degenerate)."""
    pairs = [
        OutcomePair(decision_id=f"d_{i}", predicted_probability=0.7, actual_outcome=1)
        for i in range(150)
    ]
    report = walk_forward_validate(pairs)
    assert isinstance(report, WalkForwardReport)
    if report.splits:
        # In an all-1 world the identity calibrator is correct: stays identity
        assert all(s.calibrator_is_identity for s in report.splits)
