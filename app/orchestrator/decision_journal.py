"""KAI Decision Journal - canonical DecisionRecord-backed audit surface.

The journal keeps its public CLI/MCP helper names for compatibility, but all
payloads now converge onto app.execution.models.DecisionRecord as the single
runtime backbone. Legacy rows are normalized into the canonical schema before
validation; malformed rows fail closed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.enums import ExecutionMode
from app.execution.models import (
    ApprovalState,
    DecisionExecutionState,
    DecisionLogicBlock,
    DecisionRecord,
    DecisionRiskAssessment,
    append_decision_record_jsonl,
    validate_decision_record_payload,
)

logger = logging.getLogger(__name__)

DECISION_JOURNAL_JSONL_FILENAME = "decision_journal.jsonl"
DEFAULT_DECISION_JOURNAL_PATH = f"artifacts/{DECISION_JOURNAL_JSONL_FILENAME}"

_LEGACY_APPROVAL_STATE_MAP = {
    "auto_approved_paper": ApprovalState.NOT_REQUIRED.value,
}
_LEGACY_EXECUTION_STATE_MAP = {
    "submitted": DecisionExecutionState.QUEUED.value,
    "filled": DecisionExecutionState.EXECUTED.value,
    "partial": DecisionExecutionState.BLOCKED.value,
    "cancelled": DecisionExecutionState.FAILED.value,
    "rejected": DecisionExecutionState.FAILED.value,
    "error": DecisionExecutionState.FAILED.value,
}


@dataclass(frozen=True)
class RiskAssessment:
    """Compatibility wrapper for older journal creation paths."""

    risk_level: str
    max_position_pct: float
    drawdown_remaining_pct: float
    kill_switch_active: bool = False
    summary: str | None = None
    blocked_reasons: tuple[str, ...] = ()
    advisory_notes: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary or _build_risk_summary(self),
            "risk_level": self.risk_level,
            "blocked_reasons": list(self.blocked_reasons),
            "advisory_notes": list(self.advisory_notes),
            "max_position_pct": self.max_position_pct,
            "drawdown_remaining_pct": self.drawdown_remaining_pct,
            "kill_switch_active": self.kill_switch_active,
        }


type DecisionInstance = DecisionRecord


@dataclass(frozen=True)
class DecisionJournalSummary:
    """Read-only summary of append-only decision records."""

    generated_at: str
    journal_path: str
    total_count: int
    by_mode: dict[str, int]
    by_approval: dict[str, int]
    by_execution: dict[str, int]
    symbols: list[str]
    latest_decision_id: str | None = None
    latest_timestamp: str | None = None
    avg_confidence: float | None = None
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "decision_journal_summary",
            "generated_at": self.generated_at,
            "journal_path": self.journal_path,
            "total_count": self.total_count,
            "by_mode": dict(self.by_mode),
            "by_approval": dict(self.by_approval),
            "by_execution": dict(self.by_execution),
            "symbols": list(self.symbols),
            "latest_decision_id": self.latest_decision_id,
            "latest_timestamp": self.latest_timestamp,
            "avg_confidence": self.avg_confidence,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


def _require_non_blank(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must be a non-empty string")
    return normalized


def _require_string_list(values: list[str] | tuple[str, ...] | None, *, label: str) -> list[str]:
    if values is None:
        return []
    result: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError(f"{label} entries must be strings")
        normalized = raw.strip()
        if normalized:
            result.append(normalized)
    return result


def _coerce_mode(value: str | ExecutionMode) -> ExecutionMode:
    if isinstance(value, ExecutionMode):
        return value
    normalized = value.strip().lower()
    try:
        return ExecutionMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in ExecutionMode)
        raise ValueError(f"mode must be one of: {allowed}") from exc


def _coerce_approval_state(value: str | ApprovalState) -> ApprovalState:
    if isinstance(value, ApprovalState):
        return value
    normalized = value.strip().lower()
    normalized = _LEGACY_APPROVAL_STATE_MAP.get(normalized, normalized)
    try:
        return ApprovalState(normalized)
    except ValueError as exc:
        allowed = ", ".join(state.value for state in ApprovalState)
        raise ValueError(f"approval_state must be one of: {allowed}") from exc


def _default_execution_state(mode: ExecutionMode) -> DecisionExecutionState:
    if mode is ExecutionMode.PAPER:
        return DecisionExecutionState.PAPER_ONLY
    if mode is ExecutionMode.SHADOW:
        return DecisionExecutionState.SHADOW_ONLY
    if mode is ExecutionMode.LIVE:
        return DecisionExecutionState.BLOCKED
    return DecisionExecutionState.NOT_EXECUTABLE


def _coerce_execution_state(
    value: str | DecisionExecutionState | None,
    *,
    mode: ExecutionMode,
) -> DecisionExecutionState:
    if value is None:
        return _default_execution_state(mode)
    if isinstance(value, DecisionExecutionState):
        return value
    normalized = value.strip().lower()
    if normalized == "pending":
        return _default_execution_state(mode)
    normalized = _LEGACY_EXECUTION_STATE_MAP.get(normalized, normalized)
    try:
        return DecisionExecutionState(normalized)
    except ValueError as exc:
        allowed = ", ".join(state.value for state in DecisionExecutionState)
        raise ValueError(f"execution_state must be one of: {allowed}") from exc


def _build_risk_summary(risk: RiskAssessment) -> str:
    return (
        f"risk_level={risk.risk_level}; "
        f"max_position_pct={risk.max_position_pct:.4f}; "
        f"drawdown_remaining_pct={risk.drawdown_remaining_pct:.4f}"
    )


def _coerce_risk_assessment(
    risk_assessment: RiskAssessment | DecisionRiskAssessment,
) -> DecisionRiskAssessment:
    if isinstance(risk_assessment, DecisionRiskAssessment):
        return risk_assessment
    blocked_reasons = risk_assessment.blocked_reasons
    if risk_assessment.kill_switch_active and "kill_switch_active" not in blocked_reasons:
        blocked_reasons = (*blocked_reasons, "kill_switch_active")
    return DecisionRiskAssessment(
        summary=risk_assessment.summary or _build_risk_summary(risk_assessment),
        risk_level=_require_non_blank(risk_assessment.risk_level, label="risk_level"),
        blocked_reasons=blocked_reasons,
        advisory_notes=risk_assessment.advisory_notes,
        max_position_pct=risk_assessment.max_position_pct,
        drawdown_remaining_pct=risk_assessment.drawdown_remaining_pct,
        kill_switch_active=risk_assessment.kill_switch_active,
    )


def _build_logic_block(value: str | DecisionLogicBlock, *, label: str) -> DecisionLogicBlock:
    if isinstance(value, DecisionLogicBlock):
        return value
    summary = _require_non_blank(value, label=label)
    return DecisionLogicBlock(summary=summary, conditions=(summary,))


def create_decision_instance(
    *,
    symbol: str,
    market: str,
    venue: str,
    mode: str | ExecutionMode,
    thesis: str,
    supporting_factors: list[str],
    contradictory_factors: list[str] | None = None,
    confidence_score: float,
    market_regime: str,
    volatility_state: str,
    liquidity_state: str,
    risk_assessment: RiskAssessment | DecisionRiskAssessment,
    entry_logic: str | DecisionLogicBlock,
    exit_logic: str | DecisionLogicBlock,
    stop_loss: float | None,
    take_profit: float | None = None,
    invalidation_condition: str,
    position_size_rationale: str,
    max_loss_estimate: float,
    data_sources_used: list[str],
    model_version: str,
    prompt_version: str,
    approval_state: str | ApprovalState = ApprovalState.AUDIT_ONLY,
    execution_state: str | DecisionExecutionState | None = None,
    timestamp_utc: str | None = None,
    decision_id: str | None = None,
) -> DecisionInstance:
    """Create a validated canonical DecisionRecord for journal storage."""

    normalized_mode = _coerce_mode(mode)
    normalized_thesis = _require_non_blank(thesis, label="thesis")
    if len(normalized_thesis) < 10:
        raise ValueError("thesis must be at least 10 characters")

    supporting = _require_string_list(
        supporting_factors,
        label="supporting_factors",
    )
    if not supporting:
        raise ValueError("supporting_factors must have at least one entry")

    payload: dict[str, object] = {
        "symbol": _require_non_blank(symbol, label="symbol"),
        "market": _require_non_blank(market, label="market"),
        "venue": _require_non_blank(venue, label="venue"),
        "mode": normalized_mode,
        "thesis": normalized_thesis,
        "supporting_factors": tuple(supporting),
        "contradictory_factors": tuple(
            _require_string_list(
                contradictory_factors,
                label="contradictory_factors",
            )
        ),
        "confidence_score": confidence_score,
        "market_regime": _require_non_blank(market_regime, label="market_regime"),
        "volatility_state": _require_non_blank(
            volatility_state,
            label="volatility_state",
        ),
        "liquidity_state": _require_non_blank(
            liquidity_state,
            label="liquidity_state",
        ),
        "risk_assessment": _coerce_risk_assessment(risk_assessment),
        "entry_logic": _build_logic_block(entry_logic, label="entry_logic"),
        "exit_logic": _build_logic_block(exit_logic, label="exit_logic"),
        "stop_loss": stop_loss if stop_loss and stop_loss > 0 else None,
        "take_profit": take_profit if take_profit and take_profit > 0 else None,
        "invalidation_condition": _require_non_blank(
            invalidation_condition,
            label="invalidation_condition",
        ),
        "position_size_rationale": _require_non_blank(
            position_size_rationale,
            label="position_size_rationale",
        ),
        "max_loss_estimate": max(max_loss_estimate, 0.0),
        "data_sources_used": tuple(
            _require_string_list(
                data_sources_used,
                label="data_sources_used",
            )
        ),
        "model_version": _require_non_blank(model_version, label="model_version"),
        "prompt_version": _require_non_blank(prompt_version, label="prompt_version"),
        "approval_state": _coerce_approval_state(approval_state),
        "execution_state": _coerce_execution_state(
            execution_state,
            mode=normalized_mode,
        ),
    }
    if not payload["data_sources_used"]:
        raise ValueError("data_sources_used must have at least one entry")
    if timestamp_utc is not None:
        payload["timestamp_utc"] = _require_non_blank(
            timestamp_utc,
            label="timestamp_utc",
        )
    if decision_id is not None:
        payload["decision_id"] = _require_non_blank(decision_id, label="decision_id")
    return validate_decision_record_payload(payload)


def append_decision_jsonl(
    decision: DecisionInstance,
    path: Path | str,
) -> Path:
    """Append a canonical DecisionRecord without mutating prior rows."""

    resolved = Path(path)
    append_decision_record_jsonl(resolved, decision)
    return resolved


def _normalize_logic_payload(payload: dict[str, object], *, label: str) -> dict[str, object]:
    raw = payload.get(label)
    if isinstance(raw, str):
        summary = _require_non_blank(raw, label=label)
        payload[label] = {"summary": summary, "conditions": [summary]}
        return payload
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a string or object")
    normalized = dict(raw)
    summary = str(normalized.get("summary", "")).strip()
    if not summary:
        raise ValueError(f"{label}.summary must be a non-empty string")
    conditions = normalized.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        normalized["conditions"] = [summary]
    payload[label] = normalized
    return payload


def _normalize_risk_payload(payload: dict[str, object]) -> dict[str, object]:
    raw_risk = payload.get("risk_assessment", {})
    if not isinstance(raw_risk, dict):
        raise ValueError("risk_assessment must be an object")
    risk_payload = dict(raw_risk)
    summary = str(risk_payload.get("summary", "")).strip()
    risk_level = str(risk_payload.get("risk_level", "")).strip()
    if not summary:
        if not risk_level:
            raise ValueError("risk_assessment.summary or risk_assessment.risk_level required")
        summary = f"risk_level={risk_level}"
    risk_payload["summary"] = summary
    risk_payload["risk_level"] = risk_level or "unknown"
    payload["risk_assessment"] = risk_payload
    return payload


def _normalize_numeric_payload(payload: dict[str, object], *, key: str) -> dict[str, object]:
    raw = payload.get(key)
    if raw is None:
        return payload
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric or null") from exc
    payload[key] = value if value > 0 else None
    return payload


def _normalize_legacy_decision_payload(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    normalized.pop("report_type", None)

    mode = _coerce_mode(str(normalized.get("mode", "research")))
    normalized["mode"] = mode.value

    approval_raw = str(normalized.get("approval_state", ApprovalState.AUDIT_ONLY.value))
    normalized["approval_state"] = _coerce_approval_state(approval_raw).value

    execution_raw = normalized.get("execution_state")
    normalized["execution_state"] = _coerce_execution_state(
        None if execution_raw is None else str(execution_raw),
        mode=mode,
    ).value

    normalized.setdefault("contradictory_factors", [])
    normalized = _normalize_risk_payload(normalized)
    normalized = _normalize_logic_payload(normalized, label="entry_logic")
    normalized = _normalize_logic_payload(normalized, label="exit_logic")
    normalized = _normalize_numeric_payload(normalized, key="stop_loss")
    normalized = _normalize_numeric_payload(normalized, key="take_profit")

    raw_max_loss = normalized.get("max_loss_estimate", 0.0)
    try:
        normalized["max_loss_estimate"] = max(float(raw_max_loss), 0.0)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("max_loss_estimate must be numeric") from exc

    return normalized


def load_decision_journal(path: Path | str) -> list[DecisionInstance]:
    """Load append-only journal rows and fail closed on malformed records."""

    resolved = Path(path)
    if not resolved.exists():
        return []

    entries: list[DecisionInstance] = []
    for line_number, raw_line in enumerate(
        resolved.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.error(
                "Decision journal malformed at %s line %s: %s",
                resolved,
                line_number,
                exc,
            )
            raise ValueError(f"Invalid decision journal JSON at line {line_number}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid decision journal payload at line {line_number}")
        try:
            normalized = _normalize_legacy_decision_payload(dict(payload))
            entries.append(validate_decision_record_payload(normalized))
        except ValueError as exc:
            logger.error(
                "Decision journal validation failed at %s line %s: %s",
                resolved,
                line_number,
                exc,
            )
            raise ValueError(f"Invalid decision journal payload at line {line_number}") from exc
    return entries


def build_decision_journal_summary(
    entries: list[DecisionInstance],
    *,
    journal_path: Path | str = DEFAULT_DECISION_JOURNAL_PATH,
) -> DecisionJournalSummary:
    """Build a read-only summary from canonical DecisionRecord entries."""

    by_mode: dict[str, int] = {}
    by_approval: dict[str, int] = {}
    by_execution: dict[str, int] = {}
    symbols_set: set[str] = set()
    total_confidence = 0.0

    for entry in entries:
        mode = entry.mode.value
        approval = entry.approval_state.value
        execution = entry.execution_state.value
        by_mode[mode] = by_mode.get(mode, 0) + 1
        by_approval[approval] = by_approval.get(approval, 0) + 1
        by_execution[execution] = by_execution.get(execution, 0) + 1
        symbols_set.add(entry.symbol)
        total_confidence += entry.confidence_score

    avg_confidence = total_confidence / len(entries) if entries else None
    latest = entries[-1] if entries else None
    return DecisionJournalSummary(
        generated_at=datetime.now(UTC).isoformat(),
        journal_path=str(Path(journal_path)),
        total_count=len(entries),
        by_mode=by_mode,
        by_approval=by_approval,
        by_execution=by_execution,
        symbols=sorted(symbols_set),
        latest_decision_id=latest.decision_id if latest else None,
        latest_timestamp=latest.timestamp_utc if latest else None,
        avg_confidence=round(avg_confidence, 4) if avg_confidence is not None else None,
    )
