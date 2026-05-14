"""Unit tests for SignalAuditAdapter (Phase 2B — Adaptive-Learning Schritt 3).

The adapter is a thin facade — its main contract is that both sinks are
optional, and that no-op construction must not crash the signal generator.
"""

from __future__ import annotations

from pathlib import Path

from app.audit.structured_reasoning import ReasoningJournal
from app.signals.audit_adapter import SignalAuditAdapter


def test_both_sinks_none_is_silent_noop():
    adapter = SignalAuditAdapter(reasoning_journal=None, bayes_audit_path=None)
    assert adapter.is_journaling is False


def test_only_reasoning_journal_is_journaling(tmp_path: Path):
    journal = ReasoningJournal(path=tmp_path / "reasoning.jsonl")
    adapter = SignalAuditAdapter(reasoning_journal=journal, bayes_audit_path=None)
    assert adapter.is_journaling is True


def test_only_bayes_audit_path_is_journaling(tmp_path: Path):
    adapter = SignalAuditAdapter(
        reasoning_journal=None,
        bayes_audit_path=tmp_path / "bayes.jsonl",
    )
    assert adapter.is_journaling is True


def test_log_calibrator_apply_on_noop_adapter_does_not_crash():
    """The most important contract — generator code can call log_* unconditionally
    without checking journal-presence, and a no-op adapter just absorbs it."""
    adapter = SignalAuditAdapter(reasoning_journal=None, bayes_audit_path=None)
    adapter.log_calibrator_apply(
        decision_id="dec_001",
        actor="test",
        rationale_summary="no-op test",
        inputs={"raw_posterior": 0.85},
        outputs={"calibrated_posterior": 0.75},
        confidence_before=0.85,
        confidence_after=0.75,
        parameter_versions={"calibrator": "v_test"},
    )
    # No exception, no file written, no observable side-effect.


def test_log_calibrator_apply_with_journal_writes_entry(tmp_path: Path):
    journal_path = tmp_path / "reasoning.jsonl"
    journal = ReasoningJournal(path=journal_path)
    adapter = SignalAuditAdapter(reasoning_journal=journal, bayes_audit_path=None)

    adapter.log_calibrator_apply(
        decision_id="dec_002",
        actor="test",
        rationale_summary="calibrator squashes overconfidence",
        inputs={"raw_posterior": 0.85},
        outputs={"calibrated_posterior": 0.75},
        confidence_before=0.85,
        confidence_after=0.75,
        parameter_versions={"calibrator": "v_test_001"},
    )

    assert journal_path.exists()
    content = journal_path.read_text(encoding="utf-8")
    assert "dec_002" in content
    assert "calibrator squashes overconfidence" in content


def test_bayes_audit_path_accepts_str_and_path(tmp_path: Path):
    """Path | str | None constructor variants should all work without explicit cast."""
    adapter_path = SignalAuditAdapter(
        reasoning_journal=None, bayes_audit_path=tmp_path / "audit.jsonl"
    )
    adapter_str = SignalAuditAdapter(
        reasoning_journal=None, bayes_audit_path=str(tmp_path / "audit.jsonl")
    )
    assert adapter_path.is_journaling is True
    assert adapter_str.is_journaling is True
