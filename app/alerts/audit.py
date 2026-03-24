"""Audit trail for dispatched alerts.

Sprint 21 — provides an append-only log of fired alerts for the Readiness Summary
without mutating the KAI core database or tracking state within the Engine.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Default JSONL filename for alert audits
ALERT_AUDIT_JSONL_FILENAME = "alert_audit.jsonl"
# Backwards-compatibility alias
ALTER_AUDIT_JSONL_FILENAME = ALERT_AUDIT_JSONL_FILENAME


def _resolve_audit_path(path: Path) -> Path:
    """Return the concrete file path.

    If *path* is a directory the canonical ``ALERT_AUDIT_JSONL_FILENAME``
    is appended so callers can pass either a directory or a full file path.
    """
    if path.is_dir():
        return path / ALERT_AUDIT_JSONL_FILENAME
    return path


@dataclass(frozen=True)
class AlertAuditRecord:
    """Immutable audit record representing a dispatched alert."""

    document_id: str
    channel: str
    message_id: str | None
    is_digest: bool
    dispatched_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "channel": self.channel,
            "message_id": self.message_id,
            "is_digest": self.is_digest,
            "dispatched_at": self.dispatched_at,
        }


def append_alert_audit(record: AlertAuditRecord, output_path: str | Path) -> None:
    """Append an AlertAuditRecord to the designated JSONL audit file.

    *output_path* may be a directory (writes to
    ``<dir>/ALERT_AUDIT_JSONL_FILENAME``) or a full file path.
    """
    p = _resolve_audit_path(Path(output_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_json_dict()) + "\n")


def load_alert_audits(input_path: str | Path) -> list[AlertAuditRecord]:
    """Load existing alert audit records from the JSONL audit file.

    *input_path* may be a directory (reads
    ``<dir>/ALERT_AUDIT_JSONL_FILENAME``) or a full file path.
    """
    p = _resolve_audit_path(Path(input_path))
    if not p.exists():
        return []

    records: list[AlertAuditRecord] = []
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            record = AlertAuditRecord(
                document_id=data["document_id"],
                channel=data["channel"],
                message_id=data.get("message_id"),
                is_digest=data.get("is_digest", False),
                dispatched_at=data["dispatched_at"],
            )
            records.append(record)
        except (json.JSONDecodeError, KeyError):
            continue
    return records
