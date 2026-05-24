"""F3-V-0 (Sprint 2026-05-24) — directional_confidence persistence in AlertAuditRecord.

Round-trip tests for the new field added to alert_audit.jsonl. See memo
`artifacts/operator_memos/f3_confidence_recalibration_blocked_2026-05-24.md`
for sprint context.
"""

from __future__ import annotations

from pathlib import Path

from app.alerts.audit import (
    AlertAuditRecord,
    append_alert_audit,
    load_alert_audits,
)


def test_alert_audit_record_persists_directional_confidence(tmp_path: Path) -> None:
    """directional_confidence survives a write+read round-trip."""
    record = AlertAuditRecord(
        document_id="d1",
        channel="telegram",
        message_id="42",
        is_digest=False,
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        priority=10,
        actionable=True,
        directional_confidence=0.88,
    )
    append_alert_audit(record, tmp_path)
    loaded = load_alert_audits(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].directional_confidence == 0.88


def test_alert_audit_record_no_confidence_omits_field(tmp_path: Path) -> None:
    """Omitting directional_confidence keeps the field out of the JSON output."""
    record = AlertAuditRecord(
        document_id="d_legacy",
        channel="telegram",
        message_id=None,
        is_digest=False,
    )
    append_alert_audit(record, tmp_path)
    text = (tmp_path / "alert_audit.jsonl").read_text(encoding="utf-8")
    assert "directional_confidence" not in text


def test_alert_audit_record_load_legacy_without_confidence(tmp_path: Path) -> None:
    """Pre-V-0 audit records (no directional_confidence field) load cleanly."""
    target = tmp_path / "alert_audit.jsonl"
    target.write_text(
        '{"document_id": "legacy", "channel": "telegram", "message_id": null, '
        '"is_digest": false, "dispatched_at": "2026-05-01T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    loaded = load_alert_audits(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].directional_confidence is None


def test_alert_audit_record_confidence_extreme_values(tmp_path: Path) -> None:
    """Edge values 0.0 and 1.0 round-trip without becoming None."""
    for value in (0.0, 1.0, 0.001, 0.999):
        local = tmp_path / f"{value}"
        local.mkdir()
        record = AlertAuditRecord(
            document_id=f"d_{value}",
            channel="telegram",
            message_id=None,
            is_digest=False,
            directional_confidence=value,
        )
        append_alert_audit(record, local)
        loaded = load_alert_audits(local)
        assert len(loaded) == 1
        assert loaded[0].directional_confidence == value
