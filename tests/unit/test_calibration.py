"""Unit tests für Calibration (Brier / Log-Loss / ECE / Reliability)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.learning.calibration import (
    DEFAULT_MIN_SAMPLE_FOR_JUDGMENT,
    OutcomePair,
    compute_calibration,
)


def _pair(p: float, y: int, decision_id: str = "d") -> OutcomePair:
    return OutcomePair(decision_id=decision_id, predicted_probability=p, actual_outcome=y)


# ─── Validation ──────────────────────────────────────────────────────────────


class TestPairValidation:
    def test_actual_outcome_must_be_zero_or_one(self) -> None:
        with pytest.raises(ValidationError):
            OutcomePair(decision_id="x", predicted_probability=0.5, actual_outcome=2)

    def test_predicted_probability_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OutcomePair(decision_id="x", predicted_probability=1.5, actual_outcome=1)
        with pytest.raises(ValidationError):
            OutcomePair(decision_id="x", predicted_probability=-0.1, actual_outcome=0)

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OutcomePair(
                decision_id="x", predicted_probability=0.5, actual_outcome=1, weight=-1.0
            )


# ─── Empty / Edge ────────────────────────────────────────────────────────────


class TestEmptyAndEdge:
    def test_empty_pairs_returns_zero_report(self) -> None:
        r = compute_calibration([])
        assert r.n_pairs == 0
        assert r.brier_score is None
        assert r.log_loss is None
        assert r.expected_calibration_error is None
        assert r.bins == ()
        assert r.sample_sufficient is False
        assert any("Keine" in n for n in r.notes)

    def test_single_pair_returns_brier(self) -> None:
        r = compute_calibration([_pair(0.8, 1)])
        # (0.8 - 1)² = 0.04
        assert r.brier_score == pytest.approx(0.04, abs=1e-6)
        assert r.n_pairs == 1
        assert r.sample_sufficient is False  # < 30


# ─── Brier ───────────────────────────────────────────────────────────────────


class TestBrierScore:
    def test_perfect_prediction_yields_zero_brier(self) -> None:
        pairs = [_pair(1.0, 1) for _ in range(50)] + [_pair(0.0, 0) for _ in range(50)]
        r = compute_calibration(pairs)
        assert r.brier_score == pytest.approx(0.0, abs=1e-6)

    def test_worst_case_prediction_yields_one_brier(self) -> None:
        pairs = [_pair(1.0, 0) for _ in range(50)] + [_pair(0.0, 1) for _ in range(50)]
        r = compute_calibration(pairs)
        assert r.brier_score == pytest.approx(1.0, abs=1e-6)

    def test_uninformative_50_50_predictions_yield_quarter_brier(self) -> None:
        pairs = [_pair(0.5, 1) for _ in range(50)] + [_pair(0.5, 0) for _ in range(50)]
        r = compute_calibration(pairs)
        # (0.5 − 1)² oder (0.5 − 0)² = 0.25 jeweils
        assert r.brier_score == pytest.approx(0.25, abs=1e-6)


# ─── Log-Loss ────────────────────────────────────────────────────────────────


class TestLogLoss:
    def test_log_loss_zero_for_perfect_after_clamp(self) -> None:
        pairs = [_pair(1.0, 1) for _ in range(40)] + [_pair(0.0, 0) for _ in range(40)]
        r = compute_calibration(pairs)
        # Clamping drückt log_loss auf log(1−EPS) ≈ 0, aber nicht exakt 0
        assert r.log_loss == pytest.approx(0.0, abs=1e-7)

    def test_log_loss_finite_for_extreme_wrong_prediction(self) -> None:
        # Ohne Clamp wäre log(0) = -inf
        r = compute_calibration([_pair(1.0, 0)])
        assert r.log_loss is not None
        assert math.isfinite(r.log_loss)
        assert r.log_loss > 10  # ~−log(EPS) ≈ 20.7


# ─── ECE / Bins ──────────────────────────────────────────────────────────────


class TestECEAndBins:
    def test_perfectly_calibrated_yields_low_ece(self) -> None:
        pairs: list[OutcomePair] = []
        # 10 Bins × 10 Pairs, in jedem Bin matched mean_p ≈ mean_y
        for bin_i in range(10):
            p = (bin_i + 0.5) / 10.0
            n_wins = round(p * 10)
            n_losses = 10 - n_wins
            for _ in range(n_wins):
                pairs.append(_pair(p, 1))
            for _ in range(n_losses):
                pairs.append(_pair(p, 0))
        r = compute_calibration(pairs)
        assert r.expected_calibration_error == pytest.approx(0.0, abs=0.05)
        assert r.sample_sufficient is True

    def test_overconfident_yields_high_ece(self) -> None:
        # Überall p=0.9 vorhergesagt, tatsächliche Hit-Rate = 0.5
        pairs = [_pair(0.9, i % 2) for i in range(100)]
        r = compute_calibration(pairs)
        assert r.expected_calibration_error is not None
        assert r.expected_calibration_error >= 0.35
        assert any("over" in n.lower() or "Re-Calibration" in n for n in r.notes)

    def test_bins_cover_unit_interval(self) -> None:
        r = compute_calibration([_pair(0.5, 1), _pair(0.5, 0)], n_bins=10)
        assert len(r.bins) == 10
        assert r.bins[0].lower == pytest.approx(0.0)
        assert r.bins[-1].upper == pytest.approx(1.0)

    def test_value_one_falls_into_last_bin(self) -> None:
        r = compute_calibration([_pair(1.0, 1)], n_bins=10)
        # Letztes Bin sollte n=1 enthalten
        assert r.bins[-1].n == 1
        assert r.bins[0].n == 0


# ─── Sample-Sufficiency ──────────────────────────────────────────────────────


class TestSampleSufficiency:
    def test_below_threshold_is_insufficient(self) -> None:
        pairs = [_pair(0.7, 1) for _ in range(DEFAULT_MIN_SAMPLE_FOR_JUDGMENT - 1)]
        r = compute_calibration(pairs)
        assert r.sample_sufficient is False

    def test_at_threshold_is_sufficient(self) -> None:
        pairs = [_pair(0.7, 1) for _ in range(DEFAULT_MIN_SAMPLE_FOR_JUDGMENT)]
        r = compute_calibration(pairs)
        assert r.sample_sufficient is True


# ─── Determinismus ───────────────────────────────────────────────────────────


def test_same_input_same_report() -> None:
    pairs = [_pair(0.6, i % 2) for i in range(40)]
    r1 = compute_calibration(pairs)
    r2 = compute_calibration(pairs)
    assert r1.model_dump() == r2.model_dump()
