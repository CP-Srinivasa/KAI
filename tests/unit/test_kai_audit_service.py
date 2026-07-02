"""Tests for app.audit.kai_audit_service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.audit.kai_audit_service import (
    KaiAuditService,
    KaiAuditValidationError,
)


@pytest.fixture
def audit(tmp_path: Path) -> KaiAuditService:
    return KaiAuditService(audit_path=tmp_path / "kai_audit.jsonl")


def test_append_writes_jsonl_line(audit: KaiAuditService):
    record = audit.append(
        "KAI_STATE_CHANGED",
        state="SIGNAL",
        severity="positive_watch",
        source="test",
        message="hello",
        payload={"foo": "bar"},
    )
    assert record["id"].startswith("kai_")
    assert record["payload"] == {"foo": "bar"}
    lines = audit.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["type"] == "KAI_STATE_CHANGED"
    assert parsed["state"] == "SIGNAL"


def test_append_event_validates_required_fields(audit: KaiAuditService):
    with pytest.raises(KaiAuditValidationError) as exc:
        audit.append_event({"type": "KAI_STATE_CHANGED"})
    assert "missing" in str(exc.value).lower()


def test_invalid_event_type_rejected(audit: KaiAuditService):
    with pytest.raises(KaiAuditValidationError):
        audit.append(
            "KAI_NOT_A_REAL_EVENT",
            state="IDLE",
            severity="info",
            source="t",
            message="m",
        )


def test_invalid_state_rejected(audit: KaiAuditService):
    with pytest.raises(KaiAuditValidationError):
        audit.append(
            "KAI_STATE_CHANGED",
            state="PARTY",
            severity="info",
            source="t",
            message="m",
        )


def test_invalid_severity_rejected(audit: KaiAuditService):
    with pytest.raises(KaiAuditValidationError):
        audit.append(
            "KAI_STATE_CHANGED",
            state="IDLE",
            severity="BANANA",
            source="t",
            message="m",
        )


def test_tail_returns_last_n_records(audit: KaiAuditService):
    for i in range(5):
        audit.append(
            "KAI_STATE_CHANGED",
            state="IDLE",
            severity="info",
            source="t",
            message=f"msg {i}",
        )
    last3 = audit.tail(limit=3)
    assert len(last3) == 3
    assert last3[-1]["message"] == "msg 4"


def test_tail_empty_when_file_does_not_exist(tmp_path: Path):
    svc = KaiAuditService(audit_path=tmp_path / "missing.jsonl")
    assert svc.tail() == []


def test_tail_skips_malformed_lines(audit: KaiAuditService):
    audit.append("KAI_STATE_CHANGED", state="IDLE", severity="info", source="t", message="ok")
    with audit.path.open("a", encoding="utf-8") as f:
        f.write("not-json\n")
    audit.append("KAI_STATE_CHANGED", state="IDLE", severity="info", source="t", message="ok2")
    tail = audit.tail()
    assert len(tail) == 2
    assert tail[1]["message"] == "ok2"


def test_correlation_id_is_persisted(audit: KaiAuditService):
    record = audit.append(
        "KAI_LIVETRADE_BLOCKED",
        state="WARNING",
        severity="critical",
        source="risk_guard",
        message="blocked",
        correlation_id="signal_abc123",
    )
    assert record["correlationId"] == "signal_abc123"
