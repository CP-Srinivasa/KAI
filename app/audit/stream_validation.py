"""Schema-aware read validation for Phase-0 audit JSONL streams.

PRE-D keeps the existing append-only files in place, but gives operators and
future live-execution gates one common way to distinguish usable audit rows
from malformed rows without silently losing the failure context.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.orchestrator.decision_journal import validate_decision_journal_payload
from app.signals.bayes_journal import BayesAuditEntry
from app.storage.jsonl_io import RETRY_SLEEP_SECONDS

AuditStreamName = Literal[
    "alert_audit",
    "blocked_alerts",
    "paper_execution_audit",
    "decision_journal",
    "bayes_confidence_audit",
]


@dataclass(frozen=True)
class AuditStreamIssue:
    """A single JSON or schema validation issue in an audit stream."""

    stream: AuditStreamName
    path: Path
    line_number: int
    message: str


@dataclass(frozen=True)
class AuditStreamReadResult:
    """Validated rows and non-fatal issues collected from one JSONL stream."""

    stream: AuditStreamName
    path: Path
    rows: tuple[dict[str, Any], ...]
    issues: tuple[AuditStreamIssue, ...]

    @property
    def valid_count(self) -> int:
        return len(self.rows)

    @property
    def issue_count(self) -> int:
        return len(self.issues)


class AuditStreamValidationError(ValueError):
    """Raised when strict audit-stream validation finds any issue."""

    def __init__(self, result: AuditStreamReadResult) -> None:
        self.result = result
        first = result.issues[0] if result.issues else None
        detail = f"{first.path}:{first.line_number}: {first.message}" if first else "unknown"
        super().__init__(f"{result.stream} validation failed: {detail}")


def summarize_audit_stream_result(
    result: AuditStreamReadResult,
    *,
    max_issues: int = 3,
) -> dict[str, Any]:
    """Return a compact JSON-safe validation summary for operator surfaces."""

    sample = [
        {
            "line_number": issue.line_number,
            "message": issue.message.splitlines()[0],
        }
        for issue in result.issues[:max_issues]
    ]
    return {
        "stream": result.stream,
        "path": str(result.path),
        "valid_rows": result.valid_count,
        "issue_count": result.issue_count,
        "ok": result.issue_count == 0,
        "sample_issues": sample,
    }


class _LegacyAuditRow(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class AlertAuditStreamRow(_LegacyAuditRow):
    document_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    message_id: str | None = None
    is_digest: bool = False
    dispatched_at: str = Field(min_length=1)
    sentiment_label: str | None = None
    affected_assets: list[str] = Field(default_factory=list)
    priority: int | None = None
    actionable: bool | None = None
    directional_eligible: bool | None = None
    directional_block_reason: str | None = None
    directional_blocked_assets: list[str] = Field(default_factory=list)
    title_hash: str | None = None
    normalized_title: str | None = None
    source_name: str | None = None
    directional_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    provenance: dict[str, Any] | None = None


class BlockedAlertStreamRow(_LegacyAuditRow):
    document_id: str = Field(min_length=1)
    block_reason: str = Field(min_length=1)
    blocked_at: str = Field(min_length=1)
    sentiment_label: str | None = None
    blocked_assets: list[str] = Field(default_factory=list)
    priority: int | None = None
    actionable: bool | None = None
    title_hash: str | None = None
    normalized_title: str | None = None
    source_name: str | None = None
    directional_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PaperExecutionAuditStreamRow(_LegacyAuditRow):
    schema_version: str = "v1"
    event_type: str = Field(min_length=1)
    timestamp_utc: str = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _default_legacy_schema_version(cls, value: object) -> object:
        return "v1" if value is None else value


_STREAM_VALIDATORS: dict[AuditStreamName, Callable[[dict[str, Any]], BaseModel]] = {
    "alert_audit": AlertAuditStreamRow.model_validate,
    "blocked_alerts": BlockedAlertStreamRow.model_validate,
    "paper_execution_audit": PaperExecutionAuditStreamRow.model_validate,
    "decision_journal": validate_decision_journal_payload,
    "bayes_confidence_audit": BayesAuditEntry.model_validate,
}


def load_audit_stream(
    path: str | Path,
    stream: AuditStreamName,
    *,
    tail: int | None = None,
    strict: bool = False,
) -> AuditStreamReadResult:
    """Read a JSONL audit stream and validate each object row against its schema.

    Missing files return an empty, issue-free result. Malformed JSON and schema
    errors are reported with file line numbers; valid rows are returned as plain
    JSON-compatible dictionaries so callers do not need to depend on a specific
    Pydantic/dataclass type.
    """

    resolved = Path(path)
    if not resolved.exists():
        result = AuditStreamReadResult(stream=stream, path=resolved, rows=(), issues=())
        return result

    parsed, issues, last_failed = _parse_jsonl(resolved, stream)
    if last_failed:
        time.sleep(RETRY_SLEEP_SECONDS)
        parsed, issues, _ = _parse_jsonl(resolved, stream, report_last_json_error=True)

    if tail is not None:
        if tail <= 0:
            parsed = []
        else:
            parsed = parsed[-tail:]

    rows: list[dict[str, Any]] = []
    validator = _STREAM_VALIDATORS[stream]
    for line_number, payload in parsed:
        try:
            validated = validator(payload)
        except (TypeError, ValueError, ValidationError) as exc:
            issues.append(
                AuditStreamIssue(
                    stream=stream,
                    path=resolved,
                    line_number=line_number,
                    message=str(exc),
                )
            )
            continue
        rows.append(_to_json_dict(validated))

    result = AuditStreamReadResult(
        stream=stream,
        path=resolved,
        rows=tuple(rows),
        issues=tuple(issues),
    )
    if strict and result.issues:
        raise AuditStreamValidationError(result)
    return result


def _parse_jsonl(
    path: Path,
    stream: AuditStreamName,
    *,
    report_last_json_error: bool = False,
) -> tuple[list[tuple[int, dict[str, Any]]], list[AuditStreamIssue], bool]:
    lines = path.read_text(encoding="utf-8").splitlines()
    non_empty = [
        (line_no, line.strip()) for line_no, line in enumerate(lines, start=1) if line.strip()
    ]
    if not non_empty:
        return [], [], False

    parsed: list[tuple[int, dict[str, Any]]] = []
    issues: list[AuditStreamIssue] = []
    last_line_number = non_empty[-1][0]
    last_failed = False
    for line_number, line in non_empty:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            if line_number == last_line_number and not report_last_json_error:
                last_failed = True
            else:
                issues.append(
                    AuditStreamIssue(
                        stream=stream,
                        path=path,
                        line_number=line_number,
                        message=f"invalid JSON: {exc.msg}",
                    )
                )
            continue
        if not isinstance(payload, dict):
            issues.append(
                AuditStreamIssue(
                    stream=stream,
                    path=path,
                    line_number=line_number,
                    message=f"expected JSON object, got {type(payload).__name__}",
                )
            )
            continue
        parsed.append((line_number, payload))
    return parsed, issues, last_failed


def _to_json_dict(value: BaseModel) -> dict[str, Any]:
    dumped = value.model_dump(mode="json")
    if isinstance(dumped, dict):
        return dict(dumped)
    raise TypeError(f"validated audit row is not JSON object: {type(value).__name__}")


__all__ = [
    "AuditStreamIssue",
    "AuditStreamName",
    "AuditStreamReadResult",
    "AuditStreamValidationError",
    "AlertAuditStreamRow",
    "BlockedAlertStreamRow",
    "PaperExecutionAuditStreamRow",
    "load_audit_stream",
    "summarize_audit_stream_result",
]
