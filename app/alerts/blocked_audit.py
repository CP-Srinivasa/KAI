"""Append-only audit trail for *blocked* directional alerts.

D-148 — Blocked-Alert Recall Proxy (Option A).

Directional alerts that are suppressed by the eligibility gate
(``service.py`` pre-dispatch block) never reach ``alert_audit.jsonl``.
Without a persistent record we cannot later resolve their would-have-been
price outcomes — so recall-loss caused by the gate is invisible.

This module mirrors ``alerts/audit.py`` for the blocked-alert case:
- ``BlockedAlertRecord``  — immutable snapshot of a suppressed alert.
- ``append_blocked_alert`` / ``load_blocked_alerts`` — JSONL I/O.

Outcome annotations for blocked alerts are stored separately
(``blocked_outcomes.jsonl``, Stufe 2) to keep the dispatched-alert
audit surface unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

BLOCKED_ALERTS_JSONL_FILENAME = "blocked_alerts.jsonl"
BLOCKED_OUTCOMES_JSONL_FILENAME = "blocked_outcomes.jsonl"

BlockedOutcomeLabel = Literal["hit", "miss", "inconclusive"]


def _resolve_blocked_path(path: Path) -> Path:
    if path.is_dir():
        return path / BLOCKED_ALERTS_JSONL_FILENAME
    return path


def _resolve_blocked_outcomes_path(path: Path) -> Path:
    if path.is_dir():
        return path / BLOCKED_OUTCOMES_JSONL_FILENAME
    return path


@dataclass(frozen=True)
class BlockedAlertRecord:
    """Immutable record of a directional alert suppressed before dispatch."""

    document_id: str
    block_reason: str
    blocked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    sentiment_label: str | None = None
    blocked_assets: list[str] = field(default_factory=list)
    priority: int | None = None
    actionable: bool | None = None
    title_hash: str | None = None
    normalized_title: str | None = None
    source_name: str | None = None
    # F3-V-0 (2026-05-24) — LLM directional confidence at block-time. Especially
    # useful for ``low_directional_confidence`` blocks (the exact value the
    # gate rejected); for other reasons it's still input for outcome correlation.
    directional_confidence: float | None = None

    def to_json_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "document_id": self.document_id,
            "block_reason": self.block_reason,
            "blocked_at": self.blocked_at,
        }
        if self.sentiment_label is not None:
            d["sentiment_label"] = self.sentiment_label
        if self.blocked_assets:
            d["blocked_assets"] = self.blocked_assets
        if self.priority is not None:
            d["priority"] = self.priority
        if self.actionable is not None:
            d["actionable"] = self.actionable
        if self.title_hash is not None:
            d["title_hash"] = self.title_hash
        if self.normalized_title is not None:
            d["normalized_title"] = self.normalized_title
        if self.source_name is not None:
            d["source_name"] = self.source_name
        if self.directional_confidence is not None:
            d["directional_confidence"] = self.directional_confidence
        return d


def append_blocked_alert(
    record: BlockedAlertRecord,
    output_path: str | Path,
) -> None:
    """Append a BlockedAlertRecord to the JSONL blocked-alerts file.

    *output_path* may be a directory (writes to
    ``<dir>/BLOCKED_ALERTS_JSONL_FILENAME``) or a full file path.
    """
    p = _resolve_blocked_path(Path(output_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_json_dict()) + "\n")


@dataclass(frozen=True)
class BlockedOutcomeAnnotation:
    """Would-have-been outcome for a suppressed directional alert.

    Mirrors ``AlertOutcomeAnnotation`` but lives in its own stream so the
    dispatched-alert hit-rate metric remains unaffected.
    """

    document_id: str
    outcome: BlockedOutcomeLabel
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


def append_blocked_outcome(
    annotation: BlockedOutcomeAnnotation,
    output_path: str | Path,
) -> None:
    """Append a would-have-been outcome annotation to the JSONL file."""
    p = _resolve_blocked_outcomes_path(Path(output_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(annotation.to_json_dict()) + "\n")


def load_blocked_outcomes(input_path: str | Path) -> list[BlockedOutcomeAnnotation]:
    """Load existing would-have-been outcome annotations."""
    p = _resolve_blocked_outcomes_path(Path(input_path))
    if not p.exists():
        return []

    annotations: list[BlockedOutcomeAnnotation] = []
    for line in p.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            annotations.append(
                BlockedOutcomeAnnotation(
                    document_id=data["document_id"],
                    outcome=data["outcome"],
                    annotated_at=data.get("annotated_at", datetime.now(UTC).isoformat()),
                    asset=data.get("asset"),
                    note=data.get("note"),
                )
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return annotations


def load_blocked_alerts(input_path: str | Path) -> list[BlockedAlertRecord]:
    """Load existing BlockedAlertRecord entries from the JSONL file."""
    p = _resolve_blocked_path(Path(input_path))
    if not p.exists():
        return []

    records: list[BlockedAlertRecord] = []
    for line in p.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            records.append(
                BlockedAlertRecord(
                    document_id=data["document_id"],
                    block_reason=data["block_reason"],
                    blocked_at=data["blocked_at"],
                    sentiment_label=data.get("sentiment_label"),
                    blocked_assets=data.get("blocked_assets", []),
                    priority=data.get("priority"),
                    actionable=data.get("actionable"),
                    title_hash=data.get("title_hash"),
                    normalized_title=data.get("normalized_title"),
                    source_name=data.get("source_name"),
                    directional_confidence=data.get("directional_confidence"),
                )
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return records
