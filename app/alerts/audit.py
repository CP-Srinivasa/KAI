"""Audit trail for dispatched alerts.

Sprint 21 — provides an append-only log of fired alerts for the Readiness Summary
without mutating the KAI core database or tracking state within the Engine.

AHR-1 — adds operator outcome annotations (hit / miss / inconclusive) stored in
a separate JSONL file so hit-rate can be computed without live price data.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

# NEO-P-002 (D): delay before re-reading a JSONL file when the LAST line
# failed to decode — gives the writer time to finish flushing an append.
_LAST_LINE_RETRY_SLEEP_S = 0.1


def _read_jsonl_tolerant(path: Path) -> list[dict]:
    """Read all JSON objects from a JSONL file.

    Policy:
    - Middle-of-file ``JSONDecodeError`` is skipped silently (legacy behaviour;
      mid-file corruption is rare with append-only writes).
    - If the **last** non-empty line fails to decode, sleep briefly and re-read
      the whole file once (NEO-P-002 D). append_alert_audit uses plain
      ``'a'``-mode without flock; on Windows POSIX append-atomicity is not
      guaranteed, so a reader racing with a writer can observe a partial last
      line. 100 ms of patience makes that race statistically invisible without
      introducing a file-lock dependency (deferred to Pi-migration).
    - If the last line is still unparsable after retry, it is dropped.
    """
    if not path.exists():
        return []

    def _parse(text: str) -> tuple[list[dict], bool]:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return [], False
        records: list[dict] = []
        last_idx = len(lines) - 1
        last_failed = False
        for idx, line in enumerate(lines):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                if idx == last_idx:
                    last_failed = True
                # mid-file decode errors are skipped silently (legacy policy)
        return records, last_failed

    records, last_failed = _parse(path.read_text(encoding="utf-8"))
    if last_failed:
        time.sleep(_LAST_LINE_RETRY_SLEEP_S)
        records, _ = _parse(path.read_text(encoding="utf-8"))
    return records

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
    directional_eligible: bool | None = None
    directional_block_reason: str | None = None
    directional_blocked_assets: list[str] = field(default_factory=list)
    title_hash: str | None = None
    normalized_title: str | None = None
    source_name: str | None = None

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
        if self.directional_eligible is not None:
            d["directional_eligible"] = self.directional_eligible
        if self.directional_block_reason is not None:
            d["directional_block_reason"] = self.directional_block_reason
        if self.directional_blocked_assets:
            d["directional_blocked_assets"] = self.directional_blocked_assets
        if self.title_hash is not None:
            d["title_hash"] = self.title_hash
        if self.normalized_title is not None:
            d["normalized_title"] = self.normalized_title
        if self.source_name is not None:
            d["source_name"] = self.source_name
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
    annotations: list[AlertOutcomeAnnotation] = []
    for data in _read_jsonl_tolerant(p):
        try:
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
        except KeyError:
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
    _publish_alert_fired(record)


def _publish_alert_fired(record: AlertAuditRecord) -> None:
    # NEO-P-005: fire-and-forget SSE publish. Import lazy to avoid a cycle
    # between app.alerts and app.api, and to keep audit usable in contexts
    # where the FastAPI app is never built (CLI, tests).
    try:
        from app.api.event_hub import get_default_event_hub

        get_default_event_hub().publish(
            "alert_fired",
            {
                "document_id": record.document_id,
                "channel": record.channel,
                "is_digest": record.is_digest,
                "sentiment": record.sentiment_label,
                "priority": record.priority,
                "assets": record.affected_assets,
                "dispatched_at": record.dispatched_at,
            },
        )
    except Exception:  # noqa: BLE001 — audit must never fail on a broadcast issue
        pass


def iter_alert_audit_document_ids(input_path: str | Path) -> set[str]:
    """Stream ``document_id`` values without instantiating AlertAuditRecord per row.

    ~10x cheaper than ``load_alert_audits`` when callers only need dedup-keys
    (tv-bridge idempotency-check, alerts ingestion guards). Malformed lines
    are skipped silently — same policy as ``load_alert_audits``; half-written
    last lines are retried once (NEO-P-002 D) via ``_read_jsonl_tolerant``.
    """
    p = _resolve_audit_path(Path(input_path))
    ids: set[str] = set()
    for data in _read_jsonl_tolerant(p):
        doc_id = data.get("document_id") if isinstance(data, dict) else None
        if isinstance(doc_id, str):
            ids.add(doc_id)
    return ids


def load_alert_audits(input_path: str | Path) -> list[AlertAuditRecord]:
    """Load existing alert audit records from the JSONL audit file.

    *input_path* may be a directory (reads
    ``<dir>/ALERT_AUDIT_JSONL_FILENAME``) or a full file path.
    """
    p = _resolve_audit_path(Path(input_path))
    records: list[AlertAuditRecord] = []
    for data in _read_jsonl_tolerant(p):
        try:
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
                directional_eligible=data.get("directional_eligible"),
                directional_block_reason=data.get("directional_block_reason"),
                directional_blocked_assets=data.get(
                    "directional_blocked_assets", []
                ),
                title_hash=data.get("title_hash"),
                normalized_title=data.get("normalized_title"),
                source_name=data.get("source_name"),
            )
            records.append(record)
        except KeyError:
            continue
    return records
