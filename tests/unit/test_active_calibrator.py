"""Unit tests for the runtime ActiveCalibrator loader."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from app.learning.active_calibrator import (
    DEFAULT_BAYES_CALIBRATOR_PATH,
    ActiveCalibrator,
)
from app.learning.calibration import OutcomePair
from app.learning.config_snapshot import write_snapshot
from app.learning.regime_calibration import fit_regime_calibrators

# --------------------------------------------------------------------- helpers


def _write_single_snapshot(
    tmp_path: Path,
    *,
    intercept: float,
    slope: float,
    n_fitted: int = 100,
    parameter_path: str = DEFAULT_BAYES_CALIBRATOR_PATH,
) -> None:
    write_snapshot(
        parameter_path=parameter_path,
        parameter_set={
            "intercept": intercept,
            "slope": slope,
            "n_fitted": n_fitted,
            "is_identity": abs(intercept) < 1e-6 and abs(slope - 1.0) < 1e-6,
        },
        version_id="pv_test_single",
        activated_at_utc="2026-05-09T16:00:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )


def _write_bundle_snapshot(
    tmp_path: Path,
    *,
    parameter_path: str = DEFAULT_BAYES_CALIBRATOR_PATH,
) -> None:
    rng = random.Random(11)
    pairs: list[OutcomePair] = []
    for regime in ("low_vol", "high_vol"):
        for i in range(60):
            p = rng.uniform(0.70, 0.95)
            win = rng.random() < (0.85 if regime == "low_vol" else 0.30)
            pairs.append(
                OutcomePair(
                    decision_id=f"{regime}_{i}",
                    predicted_probability=p,
                    actual_outcome=1 if win else 0,
                    regime=regime,
                )
            )
    bundle = fit_regime_calibrators(pairs, min_pairs_per_regime=30)
    write_snapshot(
        parameter_path=parameter_path,
        parameter_set=bundle.to_parameter_set(),
        version_id="pv_test_bundle",
        activated_at_utc="2026-05-09T16:05:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )


# ============================================================================
# Loading + identity fallback
# ============================================================================


def test_load_returns_identity_when_no_snapshot(tmp_path: Path):
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    assert not cal.is_active
    assert cal.version_id is None
    # Identity behavior
    assert cal.apply(0.85) == 0.85
    assert cal.apply(0.10) == 0.10


def test_load_picks_up_single_calibrator_snapshot(tmp_path: Path):
    _write_single_snapshot(tmp_path, intercept=-0.10, slope=1.0)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    assert cal.is_active
    assert cal.version_id == "pv_test_single"
    # 0.85 → −0.10 + 1.0·0.85 = 0.75
    assert cal.apply(0.85) == pytest.approx(0.75, abs=1e-6)


def test_load_picks_up_bundle_snapshot(tmp_path: Path):
    _write_bundle_snapshot(tmp_path)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    assert cal.is_active
    assert cal.version_id == "pv_test_bundle"
    # high_vol calibrator should squash more aggressively than low_vol
    p_low = cal.apply(0.85, regime="low_vol")
    p_high = cal.apply(0.85, regime="high_vol")
    assert p_high < p_low


def test_load_falls_back_to_identity_on_unrecognized_payload(tmp_path: Path):
    # write a snapshot with a payload shape the loader doesn't understand
    write_snapshot(
        parameter_path=DEFAULT_BAYES_CALIBRATOR_PATH,
        parameter_set={"completely_unknown": "field"},
        version_id="pv_broken",
        activated_at_utc="2026-05-09T16:10:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    assert not cal.is_active  # we won't trust an unknown payload
    assert cal.apply(0.85) == 0.85


def test_identity_factory_method():
    cal = ActiveCalibrator.identity()
    assert not cal.is_active
    assert cal.version_id is None
    assert cal.apply(0.30) == 0.30


# ============================================================================
# Side-aware apply
# ============================================================================


def test_apply_side_aware_long_uses_posterior_directly(tmp_path: Path):
    _write_single_snapshot(tmp_path, intercept=-0.10, slope=1.0)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    # 0.85 → 0.75, returned as-is for long
    assert cal.apply_side_aware(0.85, direction="long") == pytest.approx(0.75, abs=1e-6)


def test_apply_side_aware_short_flips_around_one_half(tmp_path: Path):
    _write_single_snapshot(tmp_path, intercept=-0.10, slope=1.0)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    # short side: signal_p = 1 - 0.20 = 0.80, calibrate → 0.70, flip back → 0.30
    out = cal.apply_side_aware(0.20, direction="short")
    assert out == pytest.approx(0.30, abs=1e-6)


def test_apply_side_aware_unknown_direction_falls_back_to_long(tmp_path: Path):
    _write_single_snapshot(tmp_path, intercept=0.0, slope=0.5)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    # 0.80 → 0.40 (long-direction route)
    out = cal.apply_side_aware(0.80, direction="neutral")
    assert out == pytest.approx(0.40, abs=1e-6)


def test_apply_clamps_to_unit_interval(tmp_path: Path):
    """A pathological calibrator pushing p above 1.0 must clamp."""
    _write_single_snapshot(tmp_path, intercept=0.5, slope=1.0)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    # 0.90 → 1.40 → clamp to 1.0
    out = cal.apply(0.90)
    assert 0.0 <= out <= 1.0


# ============================================================================
# apply_to_report
# ============================================================================


def _build_minimal_report(
    *,
    posterior: float = 0.85,
    uncertainty: float = 0.30,
):
    """Construct a minimal ConfidenceReport for testing."""
    from app.signals.bayesian_confidence import ConfidenceReport

    directional_strength = abs(2.0 * posterior - 1.0)
    confidence = directional_strength * (1.0 - uncertainty)
    return ConfidenceReport(
        prior_probability=0.5,
        posterior_probability=posterior,
        confidence_score=round(confidence, 6),
        uncertainty_score=uncertainty,
        evidence_weight=1.0,
        agreement=0.5,
        increased=(),
        decreased=(),
        neutral=(),
        discarded=(),
        residual_uncertainty_drivers=(),
    )


def test_apply_to_report_no_op_when_calibrator_inactive(tmp_path: Path):
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)  # no snapshot
    report = _build_minimal_report(posterior=0.85, uncertainty=0.30)
    out = cal.apply_to_report(report, direction="long")
    assert out is report  # exact same object — true no-op


def test_apply_to_report_squashes_overconfident_long(tmp_path: Path):
    _write_single_snapshot(tmp_path, intercept=-0.10, slope=1.0)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    report = _build_minimal_report(posterior=0.85, uncertainty=0.30)
    out = cal.apply_to_report(report, direction="long")
    assert out.posterior_probability == pytest.approx(0.75, abs=1e-6)
    # confidence drops because directional_strength dropped (|2·0.75−1| = 0.5)
    # while certainty (1−0.30 = 0.70) is unchanged
    expected_confidence = 0.5 * 0.70
    assert out.confidence_score == pytest.approx(expected_confidence, abs=1e-6)


def test_apply_to_report_preserves_non_posterior_fields(tmp_path: Path):
    _write_single_snapshot(tmp_path, intercept=-0.05, slope=1.0)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    report = _build_minimal_report(posterior=0.85, uncertainty=0.30)
    out = cal.apply_to_report(report, direction="long")
    # Calibrator does not touch evidence-side fields
    assert out.uncertainty_score == report.uncertainty_score
    assert out.evidence_weight == report.evidence_weight
    assert out.agreement == report.agreement
    assert out.prior_probability == report.prior_probability


def test_apply_to_report_is_side_aware_for_short(tmp_path: Path):
    _write_single_snapshot(tmp_path, intercept=-0.10, slope=1.0)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    # Short signal with raw posterior 0.20 → signal_p = 0.80 → calibrated 0.70
    # → returned posterior 1 - 0.70 = 0.30
    report = _build_minimal_report(posterior=0.20, uncertainty=0.20)
    out = cal.apply_to_report(report, direction="short")
    assert out.posterior_probability == pytest.approx(0.30, abs=1e-6)


def test_apply_to_report_uses_regime_in_bundle(tmp_path: Path):
    _write_bundle_snapshot(tmp_path)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    report = _build_minimal_report(posterior=0.85, uncertainty=0.30)
    low = cal.apply_to_report(report, direction="long", regime="low_vol")
    high = cal.apply_to_report(report, direction="long", regime="high_vol")
    # high_vol calibrator squashes more → posterior moves further down
    assert high.posterior_probability < low.posterior_probability


# ============================================================================
# Edge cases
# ============================================================================


def test_state_exposes_audit_metadata(tmp_path: Path):
    _write_single_snapshot(tmp_path, intercept=0.05, slope=0.92)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    assert cal.state.activated_at_utc == "2026-05-09T16:00:00+00:00"
    assert cal.state.version_id == "pv_test_single"


def test_loading_from_custom_parameter_path(tmp_path: Path):
    _write_single_snapshot(
        tmp_path,
        intercept=-0.05,
        slope=1.0,
        parameter_path="bayes.calibrator.global",
    )
    cal = ActiveCalibrator.load(
        parameter_path="bayes.calibrator.global", snapshot_dir=tmp_path
    )
    assert cal.is_active
    assert cal.parameter_path == "bayes.calibrator.global"
