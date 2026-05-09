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

import portalocker

from app.signals.models import SignalProvenance
from app.storage.jsonl_io import read_jsonl_tolerant


def _read_jsonl_tolerant(path: Path) -> list[dict]:
    """Backward-compat wrapper around :func:`app.storage.jsonl_io.read_jsonl_tolerant`.

    Kept as a private name so existing intra-module callers and tests that
    monkey-patch this symbol continue to work. New code should import
    :func:`read_jsonl_tolerant` directly. NEO-P-002 D (D-156h) retry policy
    is owned by the shared utility as of D-194.
    """
    return read_jsonl_tolerant(path)


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
    # D-125 / SAT-C-PROV-20260422-001 — persisted provenance so the quality-bar
    # phase has a beglaubigte Zuordnung instead of relying on analysis-time
    # DB joins in provenance_metrics._load_doc_metadata.
    provenance: SignalProvenance | None = None

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
        if self.provenance is not None:
            d["provenance"] = self.provenance.to_dict()
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
    # D-125 / SAT-C-PROV-20260422-001 — provenance at outcome-write time.
    # Writers resolve this from the originating AlertAuditRecord (or the TV
    # pending-signal row for synthetic ``tv:`` ids) so backdated annotations
    # can't drift away from the source attribution they were made against.
    provenance: SignalProvenance | None = None

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
        if self.provenance is not None:
            d["provenance"] = self.provenance.to_dict()
        return d


def _resolve_outcomes_path(path: Path) -> Path:
    if path.is_dir():
        return path / ALERT_OUTCOMES_JSONL_FILENAME
    return path


def append_outcome_annotation(
    annotation: AlertOutcomeAnnotation,
    output_path: str | Path,
) -> None:
    """Append an operator outcome annotation to the outcomes JSONL file.

    V-DB5 B-K2 (2026-05-09): wrapped with a cross-platform advisory file
    lock (portalocker LOCK_EX). The auto-annotator already holds a
    higher-level lock for level-changes, but other writers — manual
    `annotate`-CLI calls, alerts-blocked auto-annotate runs, and tests
    sharing the same outcomes file — could interleave bytes mid-line.
    Lock auto-releases on file close (context-manager exit), so partial
    JSONL lines from concurrent runs are no longer possible.
    """
    p = _resolve_outcomes_path(Path(output_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        portalocker.lock(f, portalocker.LOCK_EX)
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
                    annotated_at=data.get("annotated_at", datetime.now(UTC).isoformat()),
                    asset=data.get("asset"),
                    note=data.get("note"),
                    provenance=SignalProvenance.from_dict(data.get("provenance")),
                )
            )
        except KeyError:
            continue
    return annotations


def append_alert_audit(record: AlertAuditRecord, output_path: str | Path) -> None:
    """Append an AlertAuditRecord to the designated JSONL audit file.

    *output_path* may be a directory (writes to
    ``<dir>/ALERT_AUDIT_JSONL_FILENAME``) or a full file path.

    V-DB5 B-K2 (2026-05-09): wrapped with portalocker LOCK_EX so the
    Pi's parallel writers (kai-server alert dispatch + agent-worker
    re-publish) cannot interleave bytes when both fire near-simultaneous
    audits.
    """
    p = _resolve_audit_path(Path(output_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        portalocker.lock(f, portalocker.LOCK_EX)
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


def latest_provenance_by_document_id(
    input_path: str | Path,
) -> dict[str, SignalProvenance]:
    """Return a {document_id: SignalProvenance} map from the audit JSONL.

    For documents with multiple audit rows (re-dispatches, digest+single)
    the LAST occurrence wins — matches the convention in
    ``provenance_metrics`` where the latest provenance is treated as
    authoritative. Rows without provenance are skipped (so callers see
    ``KeyError``/``dict.get is None`` for legacy untagged rows and can
    fall back to the analysis-time DB join).
    """
    p = _resolve_audit_path(Path(input_path))
    out: dict[str, SignalProvenance] = {}
    for data in _read_jsonl_tolerant(p):
        if not isinstance(data, dict):
            continue
        doc_id = data.get("document_id")
        if not isinstance(doc_id, str):
            continue
        prov = SignalProvenance.from_dict(data.get("provenance"))
        if prov is not None:
            out[doc_id] = prov
    return out


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
                directional_blocked_assets=data.get("directional_blocked_assets", []),
                title_hash=data.get("title_hash"),
                normalized_title=data.get("normalized_title"),
                source_name=data.get("source_name"),
                provenance=SignalProvenance.from_dict(data.get("provenance")),
            )
            records.append(record)
        except KeyError:
            continue
    return records
