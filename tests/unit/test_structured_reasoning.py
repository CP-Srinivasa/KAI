"""Unit tests for the structured-reasoning journal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.audit.structured_reasoning import (
    GENESIS_PREV_HASH,
    PHASE_CONFIDENCE_CHANGE,
    PHASE_EVIDENCE,
    PHASE_INVALIDATION,
    PHASE_RISK_ADJUSTMENT,
    PHASE_SCORING,
    PHASE_TRIGGER,
    SCHEMA_VERSION,
    ReasoningJournal,
    ReasoningStep,
    _hash_record,
)


@pytest.fixture
def journal(tmp_path: Path) -> ReasoningJournal:
    return ReasoningJournal(tmp_path / "structured_reasoning.jsonl")


# ============================================================================
# Genesis + chain mechanics
# ============================================================================


def test_first_step_uses_genesis_prev_hash(journal: ReasoningJournal):
    step = journal.log_step(
        decision_id="dec_1",
        phase=PHASE_TRIGGER,
        actor="SignalGenerator",
        rationale_summary="news article hit watchlist",
    )
    assert step.prev_chain_hash == GENESIS_PREV_HASH
    assert step.schema_version == SCHEMA_VERSION
    assert step.step_id.startswith("rs_")


def test_chain_links_step_to_step(journal: ReasoningJournal):
    s1 = journal.log_step(
        decision_id="dec_1", phase=PHASE_TRIGGER, actor="x", rationale_summary="a"
    )
    s2 = journal.log_step(
        decision_id="dec_1", phase=PHASE_EVIDENCE, actor="x", rationale_summary="b"
    )
    s3 = journal.log_step(
        decision_id="dec_1", phase=PHASE_SCORING, actor="x", rationale_summary="c"
    )
    assert s1.prev_chain_hash == GENESIS_PREV_HASH
    assert s2.prev_chain_hash == _hash_record(s1)
    assert s3.prev_chain_hash == _hash_record(s2)


def test_verify_chain_passes_for_clean_journal(journal: ReasoningJournal):
    journal.log_step(
        decision_id="d", phase=PHASE_TRIGGER, actor="x", rationale_summary="a"
    )
    journal.log_step(
        decision_id="d", phase=PHASE_EVIDENCE, actor="x", rationale_summary="b"
    )
    ok, err = journal.verify_chain()
    assert ok, err


def test_verify_chain_detects_in_place_tamper(journal: ReasoningJournal):
    journal.log_step(
        decision_id="d", phase=PHASE_TRIGGER, actor="x", rationale_summary="a"
    )
    journal.log_step(
        decision_id="d", phase=PHASE_EVIDENCE, actor="x", rationale_summary="b"
    )
    journal.log_step(
        decision_id="d", phase=PHASE_SCORING, actor="x", rationale_summary="c"
    )
    # Tamper with the second row
    lines = journal.path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[1])
    payload["rationale_summary"] = "secretly altered"
    lines[1] = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, err = journal.verify_chain()
    assert not ok
    assert err is not None
    assert "chain broken" in err


def test_empty_journal_verifies_clean(journal: ReasoningJournal):
    ok, err = journal.verify_chain()
    assert ok
    assert err is None


# ============================================================================
# Sanitization integration
# ============================================================================


def test_secrets_in_inputs_are_redacted(journal: ReasoningJournal):
    step = journal.log_step(
        decision_id="d",
        phase=PHASE_EVIDENCE,
        actor="SignalGenerator",
        rationale_summary="external API call to ETF news source",
        inputs={
            "api_key": "sk-ant-deadbeefcafebabe1234",
            "headers": {"Authorization": "Bearer abcdef1234567890XYZ"},
        },
    )
    payload = step.model_dump(mode="json")
    flat = json.dumps(payload)
    assert "sk-ant-deadbeefcafebabe1234" not in flat
    assert "abcdef1234567890XYZ" not in flat
    assert "[REDACTED:provider_api_key]" in flat
    assert "[REDACTED:bearer_token]" in flat


def test_long_rationale_is_truncated(journal: ReasoningJournal):
    very_long = "thinking step number twenty " * 200
    step = journal.log_step(
        decision_id="d",
        phase=PHASE_SCORING,
        actor="BayesianConfidenceEngine",
        rationale_summary=very_long,
    )
    assert "chars truncated" in step.rationale_summary
    # Cap is the default 500 + the marker; without marker, ≤ 500
    head = step.rationale_summary.split("…[")[0]
    assert len(head) <= 500


def test_actor_string_is_sanitized(journal: ReasoningJournal):
    step = journal.log_step(
        decision_id="d",
        phase=PHASE_EVIDENCE,
        actor="SignalGenerator: AKIAIOSFODNN7EXAMPLE",
        rationale_summary="ok",
    )
    assert "AKIA" not in step.actor


def test_evidence_refs_are_sanitized(journal: ReasoningJournal):
    step = journal.log_step(
        decision_id="d",
        phase=PHASE_EVIDENCE,
        actor="x",
        rationale_summary="ok",
        evidence_refs=("bayes_audit:dec_xxx", "raw_token:Bearer abcdef1234567890XYZ"),
    )
    flat = json.dumps(step.model_dump(mode="json"))
    assert "abcdef1234567890XYZ" not in flat


# ============================================================================
# Phase + schema enforcement
# ============================================================================


def test_invalid_phase_is_rejected_by_schema(journal: ReasoningJournal):
    """Phase must be one of the six declared literals."""
    with pytest.raises(ValueError):
        journal.log_step(
            decision_id="d",
            phase="random_phase",  # type: ignore[arg-type]
            actor="x",
            rationale_summary="ok",
        )


def test_all_six_phases_round_trip(journal: ReasoningJournal):
    phases = [
        PHASE_TRIGGER,
        PHASE_EVIDENCE,
        PHASE_SCORING,
        PHASE_RISK_ADJUSTMENT,
        PHASE_CONFIDENCE_CHANGE,
        PHASE_INVALIDATION,
    ]
    for p in phases:
        journal.log_step(
            decision_id="d", phase=p, actor="x", rationale_summary=f"phase {p}"
        )
    steps = list(journal.iter_steps())
    assert len(steps) == 6
    assert [s.phase for s in steps] == phases


# ============================================================================
# Reproducibility / cross-refs / confidence-change phase
# ============================================================================


def test_confidence_change_carries_before_after_and_param_version(
    journal: ReasoningJournal,
):
    step = journal.log_step(
        decision_id="d",
        phase=PHASE_CONFIDENCE_CHANGE,
        actor="ActiveCalibrator",
        rationale_summary="bayes posterior calibrated by active calibrator",
        confidence_before=0.85,
        confidence_after=0.75,
        parameter_versions={
            "bayes.calibrator.regime_bundle": "pv_test_bundle",
        },
        evidence_refs=("bayes_audit:dec_xxx",),
    )
    assert step.confidence_before == 0.85
    assert step.confidence_after == 0.75
    assert step.parameter_versions["bayes.calibrator.regime_bundle"] == "pv_test_bundle"
    assert "bayes_audit:dec_xxx" in step.evidence_refs


def test_steps_for_decision_filters_correctly(journal: ReasoningJournal):
    journal.log_step(
        decision_id="d_a", phase=PHASE_TRIGGER, actor="x", rationale_summary="a"
    )
    journal.log_step(
        decision_id="d_b", phase=PHASE_TRIGGER, actor="x", rationale_summary="b"
    )
    journal.log_step(
        decision_id="d_a", phase=PHASE_SCORING, actor="x", rationale_summary="a2"
    )
    a_steps = journal.steps_for_decision("d_a")
    assert len(a_steps) == 2
    assert all(s.decision_id == "d_a" for s in a_steps)


def test_round_trip_from_disk(journal: ReasoningJournal, tmp_path: Path):
    journal.log_step(
        decision_id="d",
        phase=PHASE_EVIDENCE,
        actor="x",
        rationale_summary="thesis",
        inputs={"foo": 42, "bar": "qux"},
        outputs={"score": 0.85},
        evidence_refs=("bayes_audit:dec_x",),
    )
    journal.log_step(
        decision_id="d",
        phase=PHASE_INVALIDATION,
        actor="RiskEngine",
        rationale_summary="kill switch active",
    )
    # Re-open and verify
    fresh = ReasoningJournal(journal.path)
    steps = list(fresh.iter_steps())
    assert len(steps) == 2
    ok, _ = fresh.verify_chain()
    assert ok


def test_step_is_immutable():
    """ReasoningStep is a frozen Pydantic model — assignment must raise."""
    from pydantic import ValidationError

    step = ReasoningStep(
        step_id="rs_test1234567",
        decision_id="d",
        timestamp_utc="2026-05-09T18:00:00+00:00",
        phase=PHASE_TRIGGER,
        actor="x",
        rationale_summary="ok",
        prev_chain_hash="0" * 64,
    )
    with pytest.raises(ValidationError):
        step.actor = "tampered"  # type: ignore[misc]


def test_malformed_lines_skipped_with_warning(journal: ReasoningJournal):
    journal.log_step(
        decision_id="d", phase=PHASE_TRIGGER, actor="x", rationale_summary="a"
    )
    with journal.path.open("a", encoding="utf-8") as fh:
        fh.write("this-is-not-json\n")
    journal.log_step(
        decision_id="d", phase=PHASE_EVIDENCE, actor="x", rationale_summary="b"
    )
    steps = list(journal.iter_steps())
    assert len(steps) == 2
