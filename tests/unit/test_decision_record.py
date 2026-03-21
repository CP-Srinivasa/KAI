from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.enums import ExecutionMode
from app.execution.models import (
    ApprovalState,
    DecisionExecutionState,
    DecisionLogicBlock,
    DecisionRecord,
    DecisionRiskAssessment,
    append_decision_record_jsonl,
    load_decision_records,
    validate_decision_record_payload,
)


def _sample_decision(**overrides: object) -> DecisionRecord:
    payload: dict[str, object] = {
        "symbol": "BTC/USDT",
        "market": "crypto",
        "venue": "paper_binance",
        "mode": ExecutionMode.PAPER,
        "thesis": "Momentum remains constructive above local support.",
        "supporting_factors": ("higher highs", "volume expansion"),
        "contradictory_factors": ("macro event risk",),
        "confidence_score": 0.82,
        "market_regime": "trend",
        "volatility_state": "elevated",
        "liquidity_state": "healthy",
        "risk_assessment": DecisionRiskAssessment(
            summary="Contained risk under configured stop-loss.",
            risk_level="moderate",
            blocked_reasons=(),
            advisory_notes=("paper-only",),
        ),
        "entry_logic": DecisionLogicBlock(
            summary="Enter on confirmation above resistance.",
            conditions=("close above resistance", "spread within threshold"),
        ),
        "exit_logic": DecisionLogicBlock(
            summary="Exit on target, stop, or invalidation.",
            conditions=("take-profit hit", "stop-loss hit", "trend breaks"),
        ),
        "stop_loss": 61250.0,
        "take_profit": 66500.0,
        "invalidation_condition": "Daily close below reclaimed breakout zone.",
        "position_size_rationale": "Risk capped by 0.25% equity rule.",
        "max_loss_estimate": 25.0,
        "data_sources_used": ("mock_market_data", "research_signals"),
        "model_version": "kai-paper-v1",
        "prompt_version": "decision-pack-v1",
        "approval_state": ApprovalState.AUDIT_ONLY,
        "execution_state": DecisionExecutionState.PAPER_ONLY,
    }
    payload.update(overrides)
    return DecisionRecord(**payload)


def test_decision_record_to_json_matches_schema_required_fields():
    record = _sample_decision()
    schema_path = Path("DECISION_SCHEMA.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    payload = record.to_json_dict()

    assert set(payload) == set(schema["required"])
    assert payload["mode"] == "paper"
    assert payload["approval_state"] == "audit_only"
    assert payload["execution_state"] == "paper_only"


def test_validate_decision_record_payload_round_trips():
    record = _sample_decision()

    validated = validate_decision_record_payload(record.to_json_dict())

    assert validated == record


def test_validate_decision_record_payload_fails_closed_on_schema_format_error():
    payload = _sample_decision().to_json_dict()
    payload["timestamp_utc"] = "not-a-timestamp"

    with pytest.raises(ValidationError, match="timestamp_utc"):
        validate_decision_record_payload(payload)


def test_live_decision_requires_explicit_approval():
    with pytest.raises(ValidationError, match="approved state"):
        _sample_decision(
            mode=ExecutionMode.LIVE,
            approval_state=ApprovalState.PENDING,
            execution_state=DecisionExecutionState.READY,
        )


def test_research_decision_stays_non_executable():
    with pytest.raises(ValidationError, match="Research decisions"):
        _sample_decision(
            mode=ExecutionMode.RESEARCH,
            execution_state=DecisionExecutionState.READY,
        )


def test_rejected_decision_can_not_be_executed():
    with pytest.raises(ValidationError, match="Rejected decisions"):
        _sample_decision(
            approval_state=ApprovalState.REJECTED,
            execution_state=DecisionExecutionState.EXECUTED,
        )


def test_decision_record_stream_is_append_only(tmp_path):
    stream_path = tmp_path / "decision_records.jsonl"
    first = _sample_decision()
    second = _sample_decision(symbol="ETH/USDT")

    append_decision_record_jsonl(stream_path, first)
    append_decision_record_jsonl(stream_path, second)
    records = load_decision_records(stream_path)

    assert [record.symbol for record in records] == ["BTC/USDT", "ETH/USDT"]
    assert len(stream_path.read_text(encoding="utf-8").splitlines()) == 2


def test_load_decision_records_fails_closed_on_malformed_row(tmp_path):
    stream_path = tmp_path / "decision_records.jsonl"
    stream_path.write_text('{"decision_id":"bad"}\nnot-json\n', encoding="utf-8")

    with pytest.raises(ValueError, match="line 1|line 2"):
        load_decision_records(stream_path)


def test_decision_record_allows_empty_contradictory_factors_for_audit_only_records():
    record = _sample_decision(
        contradictory_factors=(),
        approval_state=ApprovalState.AUDIT_ONLY,
        execution_state=DecisionExecutionState.PAPER_ONLY,
    )

    assert record.contradictory_factors == ()
