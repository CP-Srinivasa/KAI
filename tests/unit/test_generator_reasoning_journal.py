"""Integration test: SignalGenerator opt-in structured reasoning journal."""

from __future__ import annotations

from pathlib import Path

from app.audit.structured_reasoning import (
    PHASE_CONFIDENCE_CHANGE,
    PHASE_INVALIDATION,
    ReasoningJournal,
)
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


def _make_generator(
    *,
    active_calibrator: ActiveCalibrator | None = None,
    reasoning_journal: ReasoningJournal | None = None,
    static_min_bayes: float = 0.30,
):
    return SignalGenerator(
        min_confidence=0.75,
        min_confluence=2,
        bayes_engine=build_default_engine(),
        bayes_shadow_only=False,
        min_bayes_confidence=static_min_bayes,
        max_bayes_uncertainty=0.95,
        active_calibrator=active_calibrator,
        reasoning_journal=reasoning_journal,
    )


def _write_squashing_snapshot(tmp_path: Path):
    """Mild calibrator that lowers posterior by 0.10."""
    write_snapshot(
        parameter_path=DEFAULT_BAYES_CALIBRATOR_PATH,
        parameter_set={
            "intercept": -0.10,
            "slope": 1.0,
            "n_fitted": 100,
            "is_identity": False,
        },
        version_id="pv_squash_mild",
        activated_at_utc="2026-05-09T18:00:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )


# ============================================================================
# Backward compat
# ============================================================================


def test_default_constructor_works_without_reasoning_journal():
    gen = _make_generator(reasoning_journal=None)
    out = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert out is not None


def test_no_reasoning_journal_writes_when_param_is_none(tmp_path: Path):
    """Even with an active calibrator, no journal → no audit writes."""
    _write_squashing_snapshot(tmp_path)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    gen = _make_generator(active_calibrator=cal, reasoning_journal=None)
    out = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert out is not None
    # No .jsonl file should exist anywhere — explicitly verify
    journal_candidate = tmp_path / "structured_reasoning.jsonl"
    assert not journal_candidate.exists()


# ============================================================================
# Calibrator-Apply → confidence_change step
# ============================================================================


def test_calibrator_apply_emits_confidence_change_step(tmp_path: Path):
    _write_squashing_snapshot(tmp_path)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    rj = ReasoningJournal(tmp_path / "reasoning.jsonl")
    gen = _make_generator(active_calibrator=cal, reasoning_journal=rj)
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None

    steps = rj.steps_for_decision(signal.decision_id)
    confidence_steps = [s for s in steps if s.phase == PHASE_CONFIDENCE_CHANGE]
    assert len(confidence_steps) == 1
    step = confidence_steps[0]
    assert step.actor == "ActiveCalibrator"
    # Calibrator pulled posterior down by 0.10 → confidence drops too
    assert step.confidence_before is not None
    assert step.confidence_after is not None
    assert step.confidence_after < step.confidence_before
    # Parameter version is recorded for reproducibility
    assert DEFAULT_BAYES_CALIBRATOR_PATH in step.parameter_versions
    assert step.parameter_versions[DEFAULT_BAYES_CALIBRATOR_PATH] == "pv_squash_mild"
    # Cross-stream evidence ref points at the bayes audit row
    assert any(ref.startswith("bayes_audit:") for ref in step.evidence_refs)


def test_no_confidence_change_step_when_calibrator_inactive(tmp_path: Path):
    """No snapshot → ActiveCalibrator.is_active is False → no apply, no step."""
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    assert not cal.is_active
    rj = ReasoningJournal(tmp_path / "reasoning.jsonl")
    gen = _make_generator(active_calibrator=cal, reasoning_journal=rj)
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None
    confidence_steps = [s for s in rj.iter_steps() if s.phase == PHASE_CONFIDENCE_CHANGE]
    assert confidence_steps == []


# ============================================================================
# Bayes-Gate-Reject → invalidation step
# ============================================================================


def test_bayes_gate_reject_emits_invalidation_step(tmp_path: Path):
    """A super-aggressive calibrator drives confidence below the gate
    → SignalGenerator returns None and logs an invalidation step."""
    write_snapshot(
        parameter_path=DEFAULT_BAYES_CALIBRATOR_PATH,
        parameter_set={
            "intercept": -0.40,
            "slope": 0.05,
            "n_fitted": 100,
            "is_identity": False,
        },
        version_id="pv_killer",
        activated_at_utc="2026-05-09T18:10:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    rj = ReasoningJournal(tmp_path / "reasoning.jsonl")
    gen = _make_generator(active_calibrator=cal, reasoning_journal=rj)
    out = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert out is None  # gate rejected

    invalidation_steps = [s for s in rj.iter_steps() if s.phase == PHASE_INVALIDATION]
    assert len(invalidation_steps) == 1
    step = invalidation_steps[0]
    assert step.actor == "SignalGenerator.bayes_gate"
    assert step.outputs.get("reason") == "bayes_gate_rejected"
    assert "min_bayes_confidence" in step.inputs
    assert "confidence_score" in step.inputs


def test_chain_remains_valid_across_multi_decision_writes(tmp_path: Path):
    """Multiple generator calls write multiple steps to the same journal —
    the hash chain must remain intact end-to-end."""
    _write_squashing_snapshot(tmp_path)
    cal = ActiveCalibrator.load(snapshot_dir=tmp_path)
    rj = ReasoningJournal(tmp_path / "reasoning.jsonl")
    gen = _make_generator(active_calibrator=cal, reasoning_journal=rj)
    for _ in range(3):
        gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    ok, err = rj.verify_chain()
    assert ok, err
