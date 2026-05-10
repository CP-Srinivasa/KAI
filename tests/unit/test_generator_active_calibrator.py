"""Integration test: SignalGenerator opt-in active calibrator hookup."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.learning.active_calibrator import (
    DEFAULT_BAYES_CALIBRATOR_PATH,
    ActiveCalibrator,
)
from app.learning.config_snapshot import write_snapshot
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import build_default_engine
from app.signals.generator import SignalGenerator

# --------------------------------------------------------------------- helpers


def _make_analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_test",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.85,
        impact_score=0.85,
        confidence_score=0.85,
        novelty_score=0.7,
        actionable=True,
        affected_assets=["BTC", "BTC/USDT"],
        tags=["etf", "bullish"],
        spam_probability=0.05,
        explanation_short="BTC ETF approved.",
        explanation_long="Detailed.",
    )


def _make_market_data() -> MarketDataPoint:
    return MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc="2026-03-21T10:00:00+00:00",
        price=65000.0,
        volume_24h=2_000_000.0,
        change_pct_24h=3.5,
        source="mock",
    )


def _make_generator(active_calibrator=None, *, audit_path: Path | None = None):
    return SignalGenerator(
        min_confidence=0.75,
        min_confluence=2,
        bayes_engine=build_default_engine(),
        bayes_shadow_only=False,         # gate is live so calibration matters
        min_bayes_confidence=0.30,        # high enough that an aggressive
        max_bayes_uncertainty=0.95,       # squashing calibrator can knock signals out
        bayes_audit_path=audit_path,
        active_calibrator=active_calibrator,
    )


# ============================================================================
# Backward compat
# ============================================================================


def test_default_constructor_works_without_calibrator():
    gen = _make_generator(active_calibrator=None)
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None
    # Bayes report attached to the signal as before
    assert signal.bayes_posterior_probability is not None
    assert signal.bayes_confidence_score is not None


def test_inactive_calibrator_is_a_noop(tmp_path: Path):
    # No snapshot file exists in tmp_path → load() returns identity
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    assert not cal.is_active
    gen = _make_generator(active_calibrator=cal)
    baseline = _make_generator(active_calibrator=None).generate(
        _make_analysis(), _make_market_data(), "BTC/USDT"
    )
    out = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert out is not None
    assert baseline is not None
    assert out.bayes_posterior_probability == baseline.bayes_posterior_probability
    assert out.bayes_confidence_score == baseline.bayes_confidence_score


# ============================================================================
# Active calibrator effects
# ============================================================================


def _write_mild_squashing_snapshot(tmp_path: Path):
    """A calibrator that pulls every posterior down by 0.10 (mild)."""
    write_snapshot(
        parameter_path=DEFAULT_BAYES_CALIBRATOR_PATH,
        parameter_set={
            "intercept": -0.10,
            "slope": 1.0,
            "n_fitted": 100,
            "is_identity": False,
        },
        version_id="pv_squash_mild",
        activated_at_utc="2026-05-09T16:00:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )


def test_active_calibrator_lowers_signal_posterior(tmp_path: Path):
    """Apply intercept=−0.10 → posterior shrinks by 0.10 (long path).
    Mild enough that the gate still lets the signal through."""
    _write_mild_squashing_snapshot(tmp_path)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    assert cal.is_active

    baseline_signal = _make_generator(active_calibrator=None).generate(
        _make_analysis(), _make_market_data(), "BTC/USDT"
    )
    calibrated_signal = _make_generator(active_calibrator=cal).generate(
        _make_analysis(), _make_market_data(), "BTC/USDT"
    )
    assert baseline_signal is not None
    assert calibrated_signal is not None
    raw = baseline_signal.bayes_posterior_probability
    new = calibrated_signal.bayes_posterior_probability
    assert raw is not None and new is not None
    # New posterior is exactly 0.10 lower (clamped to [0, 1])
    expected = max(0.0, raw - 0.10)
    assert new == pytest.approx(expected, abs=1e-6)
    # Confidence drops too (direction-aware)
    assert calibrated_signal.bayes_confidence_score < baseline_signal.bayes_confidence_score


def test_active_calibrator_can_gate_a_signal_out(tmp_path: Path):
    """A very aggressive calibrator → confidence drops below threshold → None."""
    write_snapshot(
        parameter_path=DEFAULT_BAYES_CALIBRATOR_PATH,
        parameter_set={
            "intercept": -0.40,
            "slope": 0.05,           # extreme squash to ~0.05
            "n_fitted": 100,
            "is_identity": False,
        },
        version_id="pv_killer",
        activated_at_utc="2026-05-09T16:10:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    out = _make_generator(active_calibrator=cal).generate(
        _make_analysis(), _make_market_data(), "BTC/USDT"
    )
    # Calibrator squashes confidence below 0.30 → Bayes-gate rejects
    assert out is None


# ============================================================================
# Audit trail integrity (raw report wins)
# ============================================================================


def test_bayes_audit_records_raw_posterior_not_calibrated(tmp_path: Path):
    """The audit row must always reflect the *raw* Bayes report — otherwise
    the next learning run would feed itself calibrated data and converge
    on identity."""
    _write_mild_squashing_snapshot(tmp_path)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    audit = tmp_path / "bayes_audit.jsonl"
    gen = _make_generator(active_calibrator=cal, audit_path=audit)
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None and signal.bayes_posterior_probability is not None
    raw_baseline = _make_generator(active_calibrator=None).generate(
        _make_analysis(), _make_market_data(), "BTC/USDT"
    )
    assert raw_baseline is not None

    # Read the audit file, confirm posterior matches RAW (= baseline) not
    # the calibrated value the SignalCandidate carries.
    import json

    raw_lines = audit.read_text(encoding="utf-8").splitlines()
    assert raw_lines, "audit file should have at least one row"
    payload = json.loads(raw_lines[0])
    audit_posterior = payload["report"]["posterior_probability"]
    assert audit_posterior == pytest.approx(
        raw_baseline.bayes_posterior_probability, abs=1e-6
    )
    # And: that posterior differs from the calibrated one on the signal
    assert audit_posterior != pytest.approx(
        signal.bayes_posterior_probability, abs=1e-6
    )
