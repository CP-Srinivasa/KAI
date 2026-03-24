"""Audit trail for dispatched alerts.

Sprint 21 — provides an append-only log of fired alerts for the Readiness Summary
without mutating the KAI core database or tracking state within the Engine.

AHR-1 — adds operator outcome annotations (hit / miss / inconclusive) stored in
a separate JSONL file so hit-rate can be computed without live price data.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

# Default JSONL filename for alert audits
ALERT_AUDIT_JSONL_FILENAME = "alert_audit.jsonl"
# Backwards-compatibility alias
ALTER_AUDIT_JSONL_FILENAME = ALERT_AUDIT_JSONL_FILENAME

ALERT_OUTCOMES_JSONL_FILENAME = "alert_outcomes.jsonl"

OutcomeLabel = Literal["hit", "miss", "inconclusive"]


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
    # Prediction fields (enrichment for hit-rate metric)
    sentiment_label: str | None = None
    affected_assets: list[str] = field(default_factory=list)
    priority: int | None = None
    actionable: bool | None = None

    def to_json_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "document_id": self.document_id,
            "channel": self.channel,
            "message_id": self.message_id,
            "is_digest": self.is_digest,
            "dispatched_at": self.dispatched_at,
        }
        if self.sentiment_label is not None:
            d["sentiment_label"] = self.sentiment_label
        if self.affected_assets:
            d["affected_assets"] = self.affected_assets
        if self.priority is not None:
            d["priority"] = self.priority
        if self.actionable is not None:
            d["actionable"] = self.actionable
        return d


@dataclass(frozen=True)
class AlertOutcomeAnnotation:
    """Operator-supplied outcome for a dispatched alert.

    ``outcome`` is one of:
    - ``"hit"``           — predicted direction materialised.
    - ``"miss"``          — predicted direction did not materialise.
    - ``"inconclusive"``  — outcome ambiguous; excluded from hit-rate.
    """

    document_id: str
    outcome: OutcomeLabel
    annotated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    asset: str | None = None
    note: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "document_id": self.document_id,
            "outcome": self.outcome,
            "annotated_at": self.annotated_at,
        }
        if self.asset is not None:
            d["asset"] = self.asset
        if self.note is not None:
            d["note"] = self.note
        return d


def _resolve_outcomes_path(path: Path) -> Path:
    if path.is_dir():
        return path / ALERT_OUTCOMES_JSONL_FILENAME
    return path


def append_outcome_annotation(
    annotation: AlertOutcomeAnnotation,
    output_path: str | Path,
) -> None:
    """Append an operator outcome annotation to the outcomes JSONL file."""
    p = _resolve_outcomes_path(Path(output_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(annotation.to_json_dict()) + "\n")


def load_outcome_annotations(
    input_path: str | Path,
) -> list[AlertOutcomeAnnotation]:
    """Load operator outcome annotations from the outcomes JSONL file."""
    p = _resolve_outcomes_path(Path(input_path))
    if not p.exists():
        return []

    annotations: list[AlertOutcomeAnnotation] = []
    for line in p.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            annotations.append(
                AlertOutcomeAnnotation(
                    document_id=data["document_id"],
                    outcome=data["outcome"],
                    annotated_at=data.get(
                        "annotated_at", datetime.now(UTC).isoformat()
                    ),
                    asset=data.get("asset"),
                    note=data.get("note"),
                )
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return annotations


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
                sentiment_label=data.get("sentiment_label"),
                affected_assets=data.get("affected_assets", []),
                priority=data.get("priority"),
                actionable=data.get("actionable"),
            )
            records.append(record)
        except (json.JSONDecodeError, KeyError):
            continue
    return records
