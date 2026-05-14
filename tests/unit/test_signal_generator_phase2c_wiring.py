"""Phase 2C — SignalGenerator opt-in wiring smoke tests.

These tests verify the **wiring readiness** contract from the Phase-2C+D
spec ([[kai_phase2_cd_followup_spec_20260514]]):

1. Constructor accepts 4 new optional kwargs (`audit_adapter`,
   `active_calibrator`, `active_threshold`, `bayes_engine`) without
   breaking the legacy call surface.
2. Default behavior (all kwargs None) is identical to pre-2C behavior —
   `generate()` returns the same SignalCandidate as before.
3. When the kwargs are passed but the runtime hooks are not yet wired
   (Phase 2D is the next sprint), `generate()` still produces a valid
   candidate and does not raise.

The active Bayes-shadow + calibrator-apply logic lives in `generate()`
and will land in a follow-up step ([[kai_phase2_cd_followup_spec_20260514]]
"Phase 2C Spec" point 2-4). These tests deliberately do not assert on
audit-log side effects — that contract belongs to the next sprint.
"""

from __future__ import annotations

from pathlib import Path

from app.audit.structured_reasoning import ReasoningJournal
from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.learning.active_calibrator import ActiveCalibrator
from app.learning.active_threshold import ActiveThreshold
from app.market_data.models import MarketDataPoint
from app.signals.audit_adapter import SignalAuditAdapter
from app.signals.bayesian_confidence import build_default_engine
from app.signals.generator import SignalGenerator


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_phase2c",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.85,
        impact_score=0.85,
        confidence_score=0.85,
        novelty_score=0.7,
        actionable=True,
        affected_assets=["BTC", "BTC/USDT"],
        tags=["etf"],
        spam_probability=0.05,
        explanation_short="BTC bullish.",
        explanation_long="Detailed.",
    )


def _market() -> MarketDataPoint:
    return MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc="2026-05-14T10:00:00+00:00",
        price=65000.0,
        volume_24h=2_000_000.0,
        change_pct_24h=3.5,
        source="mock",
    )


def test_constructor_accepts_all_four_phase2c_kwargs(tmp_path: Path):
    """All four new kwargs are optional and accept their expected types."""
    journal = ReasoningJournal(path=tmp_path / "reasoning.jsonl")
    adapter = SignalAuditAdapter(
        reasoning_journal=journal, bayes_audit_path=tmp_path / "bayes.jsonl"
    )
    calibrator = ActiveCalibrator.identity()
    threshold = ActiveThreshold.load(
        parameter_path="bayes.min_confidence", default_value=0.0, snapshot_dir=tmp_path
    )
    engine = build_default_engine()

    gen = SignalGenerator(
        min_confidence=0.75,
        min_confluence=2,
        audit_adapter=adapter,
        active_calibrator=calibrator,
        active_threshold=threshold,
        bayes_engine=engine,
    )
    # Smoke: constructor stores them without raising.
    assert gen is not None


def test_default_none_kwargs_match_legacy_signal(tmp_path: Path):
    """A generator constructed with no Phase-2C kwargs produces the same
    SignalCandidate as one constructed with all four kwargs set to None.

    This is the regression contract: Phase 2C must not change the live path
    output even by accident."""
    legacy = SignalGenerator(min_confidence=0.75, min_confluence=2)
    wired_inert = SignalGenerator(
        min_confidence=0.75,
        min_confluence=2,
        audit_adapter=None,
        active_calibrator=None,
        active_threshold=None,
        bayes_engine=None,
    )

    sig_legacy = legacy.generate(_analysis(), _market(), "BTC/USDT")
    sig_inert = wired_inert.generate(_analysis(), _market(), "BTC/USDT")

    assert sig_legacy is not None
    assert sig_inert is not None
    assert sig_legacy.direction == sig_inert.direction
    assert sig_legacy.confidence_score == sig_inert.confidence_score
    assert sig_legacy.confluence_count == sig_inert.confluence_count


def test_configured_wiring_does_not_break_live_path(tmp_path: Path):
    """Even with all four Phase-2C kwargs configured, the runtime hooks
    are not yet active (Phase 2D will wire them). `generate()` must still
    produce a valid candidate and not raise."""
    journal = ReasoningJournal(path=tmp_path / "reasoning.jsonl")
    adapter = SignalAuditAdapter(
        reasoning_journal=journal, bayes_audit_path=tmp_path / "bayes.jsonl"
    )

    gen = SignalGenerator(
        min_confidence=0.75,
        min_confluence=2,
        audit_adapter=adapter,
        active_calibrator=ActiveCalibrator.identity(),
        active_threshold=ActiveThreshold.load(
            parameter_path="bayes.min_confidence", default_value=0.0, snapshot_dir=tmp_path
        ),
        bayes_engine=build_default_engine(),
    )
    sig = gen.generate(_analysis(), _market(), "BTC/USDT")
    assert sig is not None
    assert sig.direction is not None
