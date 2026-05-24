"""Tests for PRE-D schema-aware audit stream reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.audit.stream_validation import (
    AuditStreamName,
    AuditStreamValidationError,
    load_audit_stream,
)
from app.decisions.journal import RiskAssessment, create_decision_instance

_TS = "2026-05-24T10:00:00+00:00"
_STREAMS: tuple[AuditStreamName, ...] = (
    "alert_audit",
    "blocked_alerts",
    "paper_execution_audit",
    "decision_journal",
    "bayes_confidence_audit",
)


def _write_jsonl(path: Path, rows: list[dict[str, object] | str]) -> None:
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _decision_payload() -> dict[str, object]:
    decision = create_decision_instance(
        symbol="BTC/USDT",
        market="crypto",
        venue="binance_paper",
        mode="paper",
        thesis="BTC bullish breakout above resistance",
        supporting_factors=["Volume spike", "RSI divergence"],
        contradictory_factors=["High funding rate"],
        confidence_score=0.85,
        market_regime="bullish",
        volatility_state="moderate",
        liquidity_state="healthy",
        risk_assessment=RiskAssessment(
            risk_level="low",
            max_position_pct=0.25,
            drawdown_remaining_pct=95.0,
        ),
        entry_logic="Break above 68k",
        exit_logic="Trail stop at 2%",
        stop_loss=66500.0,
        take_profit=72000.0,
        invalidation_condition="Close below 65k",
        position_size_rationale="0.25% risk per trade",
        max_loss_estimate=25.0,
        data_sources_used=["CryptoPanic", "TradingView"],
        model_version="gpt-4o-2024-11-20",
        prompt_version="v1.2",
    )
    return decision.to_json_dict()


def _valid_row(stream: AuditStreamName) -> dict[str, object]:
    if stream == "alert_audit":
        return {
            "document_id": "doc-1",
            "channel": "telegram",
            "message_id": "msg-1",
            "is_digest": False,
            "dispatched_at": _TS,
            "affected_assets": ["BTC/USDT"],
            "directional_confidence": 0.82,
        }
    if stream == "blocked_alerts":
        return {
            "document_id": "doc-2",
            "block_reason": "low_directional_confidence",
            "blocked_at": _TS,
            "blocked_assets": ["ETH/USDT"],
            "directional_confidence": 0.64,
        }
    if stream == "paper_execution_audit":
        return {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": _TS,
            "symbol": "BTC/USDT",
        }
    if stream == "decision_journal":
        return _decision_payload()
    return {
        "schema_version": 1,
        "timestamp_utc": _TS,
        "decision_id": "dec_123",
        "symbol": "BTC/USDT",
        "direction": "long",
        "report": {"posterior_probability": 0.61},
    }


def _invalid_row(stream: AuditStreamName) -> dict[str, object]:
    if stream == "alert_audit":
        return {"channel": "telegram", "is_digest": False, "dispatched_at": _TS}
    if stream == "blocked_alerts":
        return {"document_id": "doc-2", "blocked_at": _TS}
    if stream == "paper_execution_audit":
        return {"schema_version": "v2", "timestamp_utc": _TS}
    if stream == "decision_journal":
        payload = _decision_payload()
        payload["confidence_score"] = 1.5
        return payload
    return {
        "schema_version": 1,
        "timestamp_utc": _TS,
        "decision_id": "dec_123",
        "symbol": "BTC/USDT",
        "direction": "long",
    }


@pytest.mark.parametrize("stream", _STREAMS)
def test_load_audit_stream_reports_schema_errors_without_dropping_valid_rows(
    tmp_path: Path,
    stream: AuditStreamName,
) -> None:
    path = tmp_path / f"{stream}.jsonl"
    _write_jsonl(path, [_valid_row(stream), _invalid_row(stream)])

    result = load_audit_stream(path, stream)

    assert result.valid_count == 1
    assert result.issue_count == 1
    assert result.issues[0].stream == stream
    assert result.issues[0].line_number == 2


def test_load_audit_stream_reports_json_errors_after_tail_retry(tmp_path: Path) -> None:
    path = tmp_path / "alert_audit.jsonl"
    _write_jsonl(path, [_valid_row("alert_audit"), "{not-json"])

    result = load_audit_stream(path, "alert_audit")

    assert result.valid_count == 1
    assert result.issue_count == 1
    assert "invalid JSON" in result.issues[0].message
    assert result.issues[0].line_number == 2


def test_load_audit_stream_strict_mode_raises_with_result(tmp_path: Path) -> None:
    path = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(path, [_invalid_row("paper_execution_audit")])

    with pytest.raises(AuditStreamValidationError) as excinfo:
        load_audit_stream(path, "paper_execution_audit", strict=True)

    assert excinfo.value.result.issue_count == 1
    assert "paper_execution_audit validation failed" in str(excinfo.value)


def test_load_audit_stream_preserves_legacy_paper_schema_default(tmp_path: Path) -> None:
    path = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(path, [{"event_type": "order_created", "timestamp_utc": _TS}])

    result = load_audit_stream(path, "paper_execution_audit")

    assert result.valid_count == 1
    assert result.rows[0]["schema_version"] == "v1"
