"""Tests for PRE-D schema-aware audit stream reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.alerts.audit import AlertAuditRecord, append_alert_audit
from app.alerts.blocked_audit import BlockedAlertRecord, append_blocked_alert
from app.audit.stream_validation import (
    AuditStreamName,
    AuditStreamValidationError,
    load_audit_stream,
)
from app.execution.models import append_decision_record_jsonl
from app.execution.paper_engine import PaperExecutionEngine
from app.orchestrator.decision_journal import RiskAssessment, create_decision_instance
from app.signals.bayes_journal import append_bayes_report
from app.signals.bayesian_confidence import ConfidenceReport

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


def test_pre_d_writers_leave_locked_schema_valid_rows(tmp_path: Path) -> None:
    alert_path = tmp_path / "alert_audit.jsonl"
    append_alert_audit(
        AlertAuditRecord(
            document_id="doc-alert",
            channel="telegram",
            message_id="msg-1",
            is_digest=False,
            dispatched_at=_TS,
        ),
        alert_path,
    )
    assert alert_path.with_suffix(".jsonl.lock").exists()
    assert load_audit_stream(alert_path, "alert_audit").issue_count == 0

    blocked_path = tmp_path / "blocked_alerts.jsonl"
    append_blocked_alert(
        BlockedAlertRecord(
            document_id="doc-blocked",
            block_reason="low_directional_confidence",
            blocked_at=_TS,
        ),
        blocked_path,
    )
    assert blocked_path.with_suffix(".jsonl.lock").exists()
    assert load_audit_stream(blocked_path, "blocked_alerts").issue_count == 0

    decision_path = tmp_path / "decision_journal.jsonl"
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
    append_decision_record_jsonl(decision_path, decision)
    assert decision_path.with_suffix(".jsonl.lock").exists()
    assert load_audit_stream(decision_path, "decision_journal").issue_count == 0

    paper_path = tmp_path / "paper_execution_audit.jsonl"
    engine = PaperExecutionEngine(audit_log_path=str(paper_path))
    engine._append_audit("order_created", {"symbol": "BTC/USDT"})
    assert paper_path.with_suffix(".jsonl.lock").exists()
    assert load_audit_stream(paper_path, "paper_execution_audit").issue_count == 0

    bayes_path = tmp_path / "bayes_confidence_audit.jsonl"
    append_bayes_report(
        decision_id="dec-bayes",
        symbol="BTC/USDT",
        direction="long",
        report=ConfidenceReport(
            prior_probability=0.5,
            posterior_probability=0.61,
            confidence_score=0.42,
            uncertainty_score=0.58,
            evidence_weight=1.2,
            agreement=0.7,
            increased=(),
            decreased=(),
            neutral=(),
            discarded=(),
            residual_uncertainty_drivers=(),
        ),
        path=bayes_path,
    )
    assert bayes_path.with_suffix(".jsonl.lock").exists()
    assert load_audit_stream(bayes_path, "bayes_confidence_audit").issue_count == 0


# ---------------------------------------------------------------------------
# Begrenztes Lesen (31.08.: der Health-Check lief OOM)
# ---------------------------------------------------------------------------


def _write_alert_stream(path: Path, count: int) -> None:
    for i in range(count):
        append_alert_audit(
            AlertAuditRecord(
                document_id=f"doc-{i:05d}",
                channel="telegram",
                message_id=f"msg-{i}",
                is_digest=False,
                dispatched_at=_TS,
            ),
            path,
        )


def test_tail_reads_only_the_newest_records(tmp_path: Path) -> None:
    path = tmp_path / "alert_audit.jsonl"
    _write_alert_stream(path, 50)
    result = load_audit_stream(path, "alert_audit", tail=10)
    assert len(result.rows) == 10
    assert result.rows[-1]["document_id"] == "doc-00049"
    assert result.rows[0]["document_id"] == "doc-00040"


def test_tail_zero_reads_nothing(tmp_path: Path) -> None:
    path = tmp_path / "alert_audit.jsonl"
    _write_alert_stream(path, 5)
    assert load_audit_stream(path, "alert_audit", tail=0).rows == ()


def test_tail_larger_than_file_returns_everything(tmp_path: Path) -> None:
    path = tmp_path / "alert_audit.jsonl"
    _write_alert_stream(path, 5)
    assert len(load_audit_stream(path, "alert_audit", tail=100).rows) == 5


def test_without_tail_everything_is_read(tmp_path: Path) -> None:
    """Positivkontrolle: die Begrenzung ist opt-in, kein stiller Default."""
    path = tmp_path / "alert_audit.jsonl"
    _write_alert_stream(path, 40)
    assert len(load_audit_stream(path, "alert_audit").rows) == 40


def test_line_numbers_stay_true_to_the_file_under_tail(tmp_path: Path) -> None:
    """Negativkontrolle: der Schnitt darf die Zeilennummer nicht verfaelschen —
    sonst zeigt ein Befund auf die falsche Zeile."""
    path = tmp_path / "alert_audit.jsonl"
    _write_alert_stream(path, 20)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{kaputt\n")
    _write_alert_stream(path, 1)
    result = load_audit_stream(path, "alert_audit", tail=5)
    assert [i.line_number for i in result.issues] == [21]


def test_bounded_read_does_not_materialise_the_whole_file(tmp_path: Path) -> None:
    """Der Kern des Fixes: der Speicher haengt am Fenster, nicht an der Datei."""
    path = tmp_path / "alert_audit.jsonl"
    _write_alert_stream(path, 500)
    result = load_audit_stream(path, "alert_audit", tail=5)
    assert len(result.rows) == 5
    assert result.rows[0]["document_id"] == "doc-00495"


def test_health_probe_tail_is_wired_and_measured() -> None:
    """Die Sonde muss die Grenze auch BENUTZEN — sonst ist sie Dekoration."""
    import inspect

    from app.alerts.health_check import SCHEMA_PROBE_TAIL, _check_audit_stream_schemas

    assert SCHEMA_PROBE_TAIL == 2000
    assert "tail=SCHEMA_PROBE_TAIL" in inspect.getsource(_check_audit_stream_schemas)
