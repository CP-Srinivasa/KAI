"""KAI Audit Service — append-only JSONL persistence for KAI events.

Spec: docs/kai_persona/technical_ui_pack_v3_2.md §12
       docs/kai_persona/final_execution_prompt_v3_4.md §14

Each entry is one validated event of a KaiAuditEventType. The frontend posts
events to /api/kai/audit, the backend (this module) writes them to
artifacts/kai_audit.jsonl with a portalocker file lock so concurrent writers
do not interleave.

Events are forward-only — no edits, no deletes. Replay is the single source
of post-mortem truth.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import portalocker

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_AUDIT_PATH = REPO_ROOT / "artifacts" / "kai_audit.jsonl"

VALID_EVENT_TYPES = (
    "KAI_STATE_CHANGED",
    "KAI_SIGNAL_RENDERED",
    "KAI_WARNING_RENDERED",
    "KAI_SECURITY_REPORT_RENDERED",
    "KAI_LIVETRADE_BLOCKED",
    "KAI_LIVETRADE_CONFIRMATION_REQUESTED",
    "KAI_ERROR_STATE_TRIGGERED",
    "KAI_AGENT_SUMMARY_RENDERED",
    "KAI_EXCHANGE_RESPONSE_RENDERED",
    "KAI_ASSET_FALLBACK_USED",
    "KAI_CONFIG_VALIDATION_FAILED",
    "KAI_TRUTH_ATTESTATION",
)

VALID_SEVERITIES = (
    "none",
    "info",
    "positive_watch",
    "system",
    "high",
    "critical",
    "unknown",
)

VALID_STATES = ("IDLE", "ANALYSIS", "SIGNAL", "WARNING", "SECURITY", "ERROR", "OFFLINE")


class KaiAuditValidationError(ValueError):
    """Raised when an event payload fails the contract."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_event_id() -> str:
    return f"kai_{uuid.uuid4().hex[:12]}"


def _validate_event(event: dict[str, Any]) -> None:
    required = ("type", "state", "severity", "source", "message")
    for key in required:
        if key not in event:
            raise KaiAuditValidationError(f"event missing required field: {key}")

    if event["type"] not in VALID_EVENT_TYPES:
        raise KaiAuditValidationError(f"invalid event type: {event['type']}")
    if event["state"] not in VALID_STATES:
        raise KaiAuditValidationError(f"invalid state: {event['state']}")
    if event["severity"] not in VALID_SEVERITIES:
        raise KaiAuditValidationError(f"invalid severity: {event['severity']}")
    if not isinstance(event.get("payload", {}), dict):
        raise KaiAuditValidationError("payload must be a mapping")


class KaiAuditService:
    """Append-only writer for KAI audit events."""

    def __init__(self, audit_path: Path = _DEFAULT_AUDIT_PATH) -> None:
        self._path = audit_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        event_type: str,
        *,
        state: str,
        severity: str,
        source: str,
        message: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        event_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Validate, enrich and append a single event. Returns the persisted record."""
        record: dict[str, Any] = {
            "id": event_id or _new_event_id(),
            "type": event_type,
            "timestamp": timestamp or _now_iso(),
            "state": state,
            "severity": severity,
            "source": source,
            "payload": payload or {},
            "message": message,
        }
        if correlation_id is not None:
            record["correlationId"] = correlation_id

        _validate_event(record)

        line = json.dumps(record, ensure_ascii=False)
        with portalocker.Lock(self._path, mode="a", encoding="utf-8") as f:
            f.write(line + "\n")
        return record

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append a pre-built event dict (used by the frontend POST handler)."""
        record = dict(event)
        record.setdefault("id", _new_event_id())
        record.setdefault("timestamp", _now_iso())
        record.setdefault("payload", {})
        _validate_event(record)
        line = json.dumps(record, ensure_ascii=False)
        with portalocker.Lock(self._path, mode="a", encoding="utf-8") as f:
            f.write(line + "\n")
        return record

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the last `limit` events (read-only convenience for /status)."""
        if not self._path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("[kai-audit] malformed line skipped in %s", self._path)
        return entries[-limit:]


_default_service: KaiAuditService | None = None


def get_default_kai_audit_service() -> KaiAuditService:
    global _default_service
    if _default_service is None:
        _default_service = KaiAuditService()
    return _default_service


def reset_default_kai_audit_service() -> None:
    """Test-only helper."""
    global _default_service
    _default_service = None
