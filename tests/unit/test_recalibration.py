"""Logit-Linear Re-Calibration: Slope/Intercept-Korrektur."""

from __future__ import annotations

import math
import random

import pytest

from app.learning.calibration import OutcomePair
from app.learning.recalibration import (
    CalibratorParameters,
    IdentityCalibrator,
    LogitLinearCalibrator,
    fit_and_score,
    fit_calibrator,
)


def _pair(p: float, y: int, decision_id: str = "d") -> OutcomePair:
    return OutcomePair(decision_id=decision_id, predicted_probability=p, actual_outcome=y)


# ─── Identity-Fallbacks ──────────────────────────────────────────────────────


def test_too_few_pairs_returns_identity() -> None:
    pairs = [_pair(0.7, 1) for _ in range(5)]
    cal = fit_calibrator(pairs)
    assert isinstance(cal, IdentityCalibrator)
    # Identity: input == output
    assert cal.transform(0.7) == 0.7


def test_all_same_predicted_returns_identity() -> None:
    pairs = [_pair(0.6, i % 2) for i in range(40)]
    cal = fit_calibrator(pairs)
    assert isinstance(cal, IdentityCalibrator)


def test_all_same_outcome_returns_identity() -> None:
    pairs = [_pair(0.3 + 0.01 * i, 1) for i in range(40)]
    cal = fit_calibrator(pairs)
    assert isinstance(cal, IdentityCalibrator)


# ─── Identity-Detection ──────────────────────────────────────────────────────


def test_well_calibrated_input_yields_near_identity() -> None:
    """Bei perfekter Kalibration ist E[y|p_raw] = p_raw → OLS findet
    slope ≈ 1, intercept ≈ 0 (im Wahrscheinlichkeitsraum)."""
    pairs: list[OutcomePair] = []
    for bin_i in range(10):
        p = (bin_i + 0.5) / 10.0
        n_wins = round(p * 10)
        for _ in range(n_wins):
            pairs.append(_pair(p, 1))
        for _ in range(10 - n_wins):
            pairs.append(_pair(p, 0))
    cal = fit_calibrator(pairs)
    assert isinstance(cal, LogitLinearCalibrator)
    assert cal.parameters.slope == pytest.approx(1.0, abs=0.05)
    assert cal.parameters.intercept == pytest.approx(0.0, abs=0.05)


# ─── Korrektur greift ────────────────────────────────────────────────────────


def test_overconfident_input_gets_compressed_after_calibration() -> None:
    """Engine sagt p ∈ [0.7, 0.95], Hit-Rate aber nur ≈ 0.5 → corrected
    Werte liegen näher an 0.5 als die Originale."""
    rng = random.Random(11)
    pairs = []
    for _ in range(100):
        p = rng.uniform(0.7, 0.95)
        y = 1 if rng.random() < 0.5 else 0
        pairs.append(_pair(p, y))
    cal = fit_calibrator(pairs)
    assert isinstance(cal, LogitLinearCalibrator)
    corrected = cal.transform(0.9)
    assert corrected < 0.9  # Komprimierung
    assert corrected > 0.0


def test_recalibration_lowers_brier_score() -> None:
    """Vorher/Nachher-Brier muss bei systematischer Fehl-Kalibration besser werden."""
    rng = random.Random(13)
    pairs = []
    for _ in range(100):
        p = rng.uniform(0.7, 0.95)
        y = 1 if rng.random() < 0.5 else 0
        pairs.append(_pair(p, y))
    out = fit_and_score(pairs)
    assert out["brier_before"] is not None and out["brier_after"] is not None
    assert out["brier_after"] <= out["brier_before"]
    assert out["improvement"] is not None and out["improvement"] >= 0.0


# ─── Transform-Vertrag ───────────────────────────────────────────────────────


def test_transform_keeps_output_in_unit_interval() -> None:
    """Selbst extreme Slope/Intercept werden in (ε, 1−ε) geclamped."""
    cal = LogitLinearCalibrator(
        parameters=CalibratorParameters(
            intercept=5.0, slope=10.0, n_fitted=100, is_identity=False, fit_notes=()
        )
    )
    for raw in [0.001, 0.5, 0.999]:
        out = cal.transform(raw)
        assert 0.0 < out < 1.0


def test_identity_calibrator_returns_input_clamped() -> None:
    cal = IdentityCalibrator()
    assert cal.transform(0.5) == 0.5
    # Werte außerhalb [0,1] werden geclamped
    assert 0.0 < cal.transform(2.0) < 1.0
    assert 0.0 < cal.transform(-1.0) < 1.0


# ─── Determinismus ───────────────────────────────────────────────────────────


def test_fit_is_deterministic() -> None:
    rng = random.Random(42)
    pairs = [_pair(rng.random(), rng.randint(0, 1)) for _ in range(50)]
    c1 = fit_calibrator(pairs)
    c2 = fit_calibrator(pairs)
    assert isinstance(c1, LogitLinearCalibrator)
    assert isinstance(c2, LogitLinearCalibrator)
    assert c1.parameters.model_dump() == c2.parameters.model_dump()


def test_fit_notes_flag_strong_drift() -> None:
    """Engine prognostiziert hoch (0.7..0.9), Hit-Rate aber nur 0.3 → Drift-Note."""
    rng = random.Random(7)
    pairs: list[OutcomePair] = []
    for _ in range(80):
        # Engine sagt hoch
        p = rng.uniform(0.7, 0.95)
        # Realer Outcome ist viel niedriger
        y = 1 if rng.random() < 0.3 else 0
        pairs.append(_pair(p, y))
    cal = fit_calibrator(pairs)
    assert isinstance(cal, LogitLinearCalibrator)
    notes_joined = " ".join(cal.parameters.fit_notes).lower()
    assert any(kw in notes_joined for kw in ("overconfidence", "bias", "underconfidence", "drift"))


def test_full_score_pipeline_with_random_input() -> None:
    rng = random.Random(0)
    # Künstliche overconfident-Engine: predicted = 0.5 + 0.4 · noise, y = bernoulli(0.5)
    pairs = []
    for i in range(80):
        p = 0.5 + 0.4 * (rng.random() - 0.5)
        y = 1 if rng.random() < 0.5 else 0
        pairs.append(_pair(p, y, f"d_{i}"))
    out = fit_and_score(pairs)
    assert math.isfinite(out["brier_before"])
    assert math.isfinite(out["brier_after"])
