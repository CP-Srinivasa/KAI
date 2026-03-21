"""Operational readiness summary built from canonical audit artifacts only.

Sprint 21 consolidates route health, collector backlog, artifact state, and
alert-dispatch visibility into one read-only report. The report adapts existing
handoff, collector, active-route, ABC-envelope, and alert-audit artifacts only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.alerts.audit import AlertAuditRecord
from app.research.abc_result import ABCInferenceEnvelope, PathResultEnvelope
from app.research.active_route import ActiveRouteState
from app.research.artifact_lifecycle import (
    REVIEW_JOURNAL_JSONL_FILENAME,
    ReviewRequiredArtifactSummary,
)
from app.research.distribution import HandoffCollectorSummaryReport
from app.research.execution_handoff import SignalHandoff, classify_delivery_for_route

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

CATEGORY_HANDOFF_BACKLOG = "handoff_backlog"
CATEGORY_ACKNOWLEDGEMENT_AUDIT = "acknowledgement_audit"
CATEGORY_ARTIFACT_STATE = "artifact_state"
CATEGORY_ROUTE_PROVIDER = "route_provider"
CATEGORY_PROVIDER_HEALTH = "provider_health"
CATEGORY_DISTRIBUTION_DRIFT = "distribution_drift"
CATEGORY_SHADOW_CONTROL_FAILURE = "shadow_control_failure"
CATEGORY_STALE_STATE = "stale_state"
CATEGORY_REVIEW_REQUIRED = "review_required"

PROVIDER_STATUS_HEALTHY = "healthy"
PROVIDER_STATUS_DEGRADED = "degraded"
PROVIDER_STATUS_UNAVAILABLE = "unavailable"

DRIFT_STATUS_NOMINAL = "nominal"
DRIFT_STATUS_WARNING = "warning"
DRIFT_STATUS_CRITICAL = "critical"

GATE_STATUS_CLEAR = "clear"
GATE_STATUS_BLOCKING = "blocking"
GATE_STATUS_WARNING = "warning"
GATE_STATUS_ADVISORY = "advisory"

ACTION_PRIORITY_P1 = "p1"
ACTION_PRIORITY_P2 = "p2"
ACTION_PRIORITY_P3 = "p3"

ACTION_QUEUE_STATUS_CLEAR = "clear"
ACTION_QUEUE_STATUS_OPEN = "open"
ACTION_QUEUE_STATUS_BLOCKING = "blocking"
ACTION_QUEUE_STATUS_REVIEW_REQUIRED = "review_required"

_SEVERITY_ORDER = {
    SEVERITY_INFO: 0,
    SEVERITY_WARNING: 1,
    SEVERITY_CRITICAL: 2,
}

_ACTION_PRIORITY_ORDER = {
    ACTION_PRIORITY_P1: 0,
    ACTION_PRIORITY_P2: 1,
    ACTION_PRIORITY_P3: 2,
}

_ACTION_QUEUE_STATUS_ORDER = {
    ACTION_QUEUE_STATUS_BLOCKING: 0,
    ACTION_QUEUE_STATUS_REVIEW_REQUIRED: 1,
    ACTION_QUEUE_STATUS_OPEN: 2,
    ACTION_QUEUE_STATUS_CLEAR: 3,
}

RUNBOOK_COMMAND_READINESS_SUMMARY = "research readiness-summary"
RUNBOOK_COMMAND_DECISION_PACK_SUMMARY = "research decision-pack-summary"
RUNBOOK_COMMAND_BLOCKING_SUMMARY = "research blocking-summary"
RUNBOOK_COMMAND_BLOCKING_ACTIONS = "research blocking-actions"
RUNBOOK_COMMAND_ACTION_QUEUE_SUMMARY = "research action-queue-summary"
RUNBOOK_COMMAND_PRIORITIZED_ACTIONS = "research prioritized-actions"
RUNBOOK_COMMAND_REVIEW_REQUIRED_ACTIONS = "research review-required-actions"
RUNBOOK_COMMAND_REVIEW_REQUIRED_SUMMARY = "research review-required-summary"
RUNBOOK_COMMAND_ARTIFACT_RETENTION = "research artifact-retention"

REVIEW_ACTION_NOTE = "note"
REVIEW_ACTION_DEFER = "defer"
REVIEW_ACTION_RESOLVE = "resolve"

JOURNAL_STATUS_EMPTY = "empty"
JOURNAL_STATUS_OPEN = "open"
JOURNAL_STATUS_RESOLVED = "resolved"

VALID_REVIEW_ACTIONS = frozenset(
    {
        REVIEW_ACTION_NOTE,
        REVIEW_ACTION_DEFER,
        REVIEW_ACTION_RESOLVE,
    }
)
DEFAULT_REVIEW_JOURNAL_PATH = f"artifacts/{REVIEW_JOURNAL_JSONL_FILENAME}"

def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _age_hours(*, earlier: datetime | None, now: datetime) -> float | None:
    if earlier is None:
        return None
    return max(0.0, (now - earlier).total_seconds() / 3600.0)


def _path_failed(path: PathResultEnvelope) -> bool:
    return (path.summary or "").strip().lower().startswith("error:")


@dataclass(frozen=True)
class ArtifactRef:
    """Single artifact reference used by the readiness report."""

    path: str | None = None
    present: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "present": self.present,
        }


@dataclass(frozen=True)
class OperationalArtifactRefs:
    """Workspace-local artifact references used to derive readiness."""

    handoff: ArtifactRef = field(default_factory=ArtifactRef)
    acknowledgements: ArtifactRef = field(default_factory=ArtifactRef)
    active_route_state: ArtifactRef = field(default_factory=ArtifactRef)
    abc_output: ArtifactRef = field(default_factory=ArtifactRef)
    alert_audit_dir: ArtifactRef = field(default_factory=ArtifactRef)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "handoff": self.handoff.to_json_dict(),
            "acknowledgements": self.acknowledgements.to_json_dict(),
            "active_route_state": self.active_route_state.to_json_dict(),
            "abc_output": self.abc_output.to_json_dict(),
            "alert_audit_dir": self.alert_audit_dir.to_json_dict(),
        }


@dataclass(frozen=True)
class ReadinessIssue:
    """Single observational readiness issue with explicit severity/category."""

    severity: str
    category: str
    summary: str
    source_ref: str | None = None
    path_id: str | None = None
    provider: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "category": self.category,
            "summary": self.summary,
            "source_ref": self.source_ref,
            "path_id": self.path_id,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class RouteReadinessSummary:
    """Derived route/provider state from ActiveRouteState plus ABC envelopes."""

    active: bool
    route_profile: str | None = None
    active_primary_path: str | None = None
    abc_output_expected: str | None = None
    abc_output_available: bool = False
    shadow_paths_configured: list[str] = field(default_factory=list)
    control_path: str | None = None
    shadow_failure_count: int = 0
    control_failure_count: int = 0
    missing_shadow_paths: list[str] = field(default_factory=list)
    missing_control_result: bool = False
    state_age_hours: float | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "route_profile": self.route_profile,
            "active_primary_path": self.active_primary_path,
            "abc_output_expected": self.abc_output_expected,
            "abc_output_available": self.abc_output_available,
            "shadow_paths_configured": list(self.shadow_paths_configured),
            "control_path": self.control_path,
            "shadow_failure_count": self.shadow_failure_count,
            "control_failure_count": self.control_failure_count,
            "missing_shadow_paths": list(self.missing_shadow_paths),
            "missing_control_result": self.missing_control_result,
            "state_age_hours": (
                round(self.state_age_hours, 2)
                if self.state_age_hours is not None
                else None
            ),
        }


@dataclass(frozen=True)
class AlertDispatchSummary:
    """Read-only summary of dispatched alert audit rows."""

    total_count: int = 0
    digest_count: int = 0
    by_channel: dict[str, int] = field(default_factory=dict)
    latest_dispatched_at: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "total_count": self.total_count,
            "digest_count": self.digest_count,
            "by_channel": dict(self.by_channel),
            "latest_dispatched_at": self.latest_dispatched_at,
        }


@dataclass(frozen=True)
class ProviderHealthEntry:
    """Read-only health row for a provider/path derived from existing artifacts."""

    provider: str
    path_id: str
    path_type: str
    status: str
    sample_count: int
    success_count: int
    failure_count: int
    expected: bool = True

    def to_json_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "path_id": self.path_id,
            "path_type": self.path_type,
            "status": self.status,
            "sample_count": self.sample_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "expected": self.expected,
        }


@dataclass(frozen=True)
class ProviderHealthSummary:
    """Aggregated provider health derived from productive and audit artifacts."""

    entries: list[ProviderHealthEntry] = field(default_factory=list)
    provider_count: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    unavailable_count: int = 0

    def to_json_dict(self) -> dict[str, object]:
        return {
            "provider_count": self.provider_count,
            "healthy_count": self.healthy_count,
            "degraded_count": self.degraded_count,
            "unavailable_count": self.unavailable_count,
            "entries": [entry.to_json_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class DistributionDriftSummary:
    """Read-only distribution drift summary derived from canonical route metadata."""

    status: str = DRIFT_STATUS_NOMINAL
    production_handoff_count: int = 0
    shadow_audit_result_count: int = 0
    control_comparison_result_count: int = 0
    expected_shadow_path_count: int = 0
    observed_shadow_path_count: int = 0
    expected_control_count: int = 0
    observed_control_count: int = 0
    unknown_path_count: int = 0
    classification_mismatch_count: int = 0
    visibility_mismatch_count: int = 0
    unexpected_visible_audit_count: int = 0

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "production_handoff_count": self.production_handoff_count,
            "shadow_audit_result_count": self.shadow_audit_result_count,
            "control_comparison_result_count": self.control_comparison_result_count,
            "expected_shadow_path_count": self.expected_shadow_path_count,
            "observed_shadow_path_count": self.observed_shadow_path_count,
            "expected_control_count": self.expected_control_count,
            "observed_control_count": self.observed_control_count,
            "unknown_path_count": self.unknown_path_count,
            "classification_mismatch_count": self.classification_mismatch_count,
            "visibility_mismatch_count": self.visibility_mismatch_count,
            "unexpected_visible_audit_count": self.unexpected_visible_audit_count,
        }


@dataclass(frozen=True)
class ProtectiveGateItem:
    """Single read-only protective gate decision derived from readiness issues."""

    gate_status: str
    severity: str
    category: str
    summary: str
    subsystem: str
    blocking_reason: str | None = None
    recommended_actions: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "gate_status": self.gate_status,
            "severity": self.severity,
            "category": self.category,
            "summary": self.summary,
            "subsystem": self.subsystem,
            "blocking_reason": self.blocking_reason,
            "recommended_actions": list(self.recommended_actions),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class ProtectiveGateSummary:
    """Read-only gate summary with operator-only remediation recommendations."""

    gate_status: str = GATE_STATUS_CLEAR
    blocking_count: int = 0
    warning_count: int = 0
    advisory_count: int = 0
    items: list[ProtectiveGateItem] = field(default_factory=list)
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "gate_status": self.gate_status,
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "advisory_count": self.advisory_count,
            "items": [item.to_json_dict() for item in self.items],
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class OperationalEscalationItem:
    """Single read-only escalation row derived from canonical readiness/governance data."""

    escalation_status: str
    severity: str
    blocking: bool
    category: str
    subsystem: str
    summary: str
    operator_action_required: bool
    blocking_reason: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "escalation_status": self.escalation_status,
            "severity": self.severity,
            "blocking": self.blocking,
            "category": self.category,
            "subsystem": self.subsystem,
            "summary": self.summary,
            "operator_action_required": self.operator_action_required,
            "blocking_reason": self.blocking_reason,
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
        }


@dataclass(frozen=True)
class OperationalEscalationSummary:
    """Read-only escalation overview derived from readiness and governance signals only."""

    escalation_status: str = GATE_STATUS_CLEAR
    severity: str = "none"
    blocking: bool = False
    blocking_count: int = 0
    warning_count: int = 0
    advisory_count: int = 0
    review_required_count: int = 0
    operator_action_count: int = 0
    items: list[OperationalEscalationItem] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "operational_escalation_summary",
            "escalation_status": self.escalation_status,
            "severity": self.severity,
            "blocking": self.blocking,
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "advisory_count": self.advisory_count,
            "review_required_count": self.review_required_count,
            "operator_action_count": self.operator_action_count,
            "items": [item.to_json_dict() for item in self.items],
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
            "generated_at": self.generated_at,
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class BlockingSummary:
    """Read-only projection of blocking escalation items only."""

    escalation_status: str = GATE_STATUS_CLEAR
    severity: str = "none"
    blocking: bool = False
    blocking_count: int = 0
    items: list[OperationalEscalationItem] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "blocking_summary",
            "escalation_status": self.escalation_status,
            "severity": self.severity,
            "blocking": self.blocking,
            "blocking_count": self.blocking_count,
            "items": [item.to_json_dict() for item in self.items],
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class ActionQueueItem:
    """Single read-only action queue row derived from operator-action escalation."""

    action_id: str
    severity: str
    priority: str
    queue_status: str
    category: str
    subsystem: str
    summary: str
    operator_action_required: bool
    blocking: bool = False
    blocking_reason: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "severity": self.severity,
            "priority": self.priority,
            "queue_status": self.queue_status,
            "category": self.category,
            "subsystem": self.subsystem,
            "summary": self.summary,
            "operator_action_required": self.operator_action_required,
            "blocking": self.blocking,
            "blocking_reason": self.blocking_reason,
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
        }


@dataclass(frozen=True)
class ActionQueueSummary:
    """Prioritized read-only queue of operator actions derived from escalation only."""

    queue_status: str = ACTION_QUEUE_STATUS_CLEAR
    total_count: int = 0
    open_count: int = 0
    blocking_count: int = 0
    review_required_count: int = 0
    highest_priority: str = "none"
    items: list[ActionQueueItem] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    report_type: str = "action_queue_summary"
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": self.report_type,
            "queue_status": self.queue_status,
            "total_count": self.total_count,
            "open_count": self.open_count,
            "blocking_count": self.blocking_count,
            "review_required_count": self.review_required_count,
            "highest_priority": self.highest_priority,
            "items": [item.to_json_dict() for item in self.items],
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
            "generated_at": self.generated_at,
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class BlockingActionsSummary:
    """Read-only projection of blocking action queue entries only."""

    queue_status: str = ACTION_QUEUE_STATUS_CLEAR
    blocking_count: int = 0
    highest_priority: str = "none"
    items: list[ActionQueueItem] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "blocking_actions_summary",
            "queue_status": self.queue_status,
            "blocking_count": self.blocking_count,
            "highest_priority": self.highest_priority,
            "items": [item.to_json_dict() for item in self.items],
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class PrioritizedActionsSummary:
    """Read-only projection of action queue entries in derived priority order."""

    queue_status: str = ACTION_QUEUE_STATUS_CLEAR
    action_count: int = 0
    highest_priority: str = "none"
    items: list[ActionQueueItem] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "prioritized_actions_summary",
            "queue_status": self.queue_status,
            "action_count": self.action_count,
            "highest_priority": self.highest_priority,
            "items": [item.to_json_dict() for item in self.items],
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class ReviewRequiredActionsSummary:
    """Read-only projection of review-required action queue entries only."""

    queue_status: str = ACTION_QUEUE_STATUS_CLEAR
    review_required_count: int = 0
    highest_priority: str = "none"
    items: list[ActionQueueItem] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "review_required_actions_summary",
            "queue_status": self.queue_status,
            "review_required_count": self.review_required_count,
            "highest_priority": self.highest_priority,
            "items": [item.to_json_dict() for item in self.items],
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class OperatorActionSummary:
    """Read-only projection of escalation items requiring human operator action."""

    escalation_status: str = GATE_STATUS_CLEAR
    severity: str = "none"
    blocking: bool = False
    operator_action_count: int = 0
    review_required_count: int = 0
    items: list[OperationalEscalationItem] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "operator_action_summary",
            "escalation_status": self.escalation_status,
            "severity": self.severity,
            "blocking": self.blocking,
            "operator_action_count": self.operator_action_count,
            "review_required_count": self.review_required_count,
            "items": [item.to_json_dict() for item in self.items],
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class OperatorDecisionPack:
    """Read-only bundle of canonical operator-facing summaries only.

    Bundles readiness status, escalation, action queue, and governance signals
    into a single human-readable situation-awareness surface.

    Advisory only. No execution authority. No auto-remediation. No routing
    control. No trading decision. Sub-report surfaces remain the source of
    truth; this pack is a derived aggregate snapshot only.
    I-185–I-192.
    """

    overall_status: str = GATE_STATUS_CLEAR
    blocking_count: int = 0
    review_required_count: int = 0
    action_queue_count: int = 0
    affected_subsystems: list[str] = field(default_factory=list)
    operator_guidance: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    readiness_summary: OperationalReadinessReport | None = None
    blocking_summary: BlockingSummary | None = None
    action_queue_summary: ActionQueueSummary | None = None
    review_required_summary: ReviewRequiredArtifactSummary | None = None
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    report_type: str = "operator_decision_pack"
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": self.report_type,
            "overall_status": self.overall_status,
            "blocking_count": self.blocking_count,
            "review_required_count": self.review_required_count,
            "action_queue_count": self.action_queue_count,
            "affected_subsystems": list(self.affected_subsystems),
            "operator_guidance": list(self.operator_guidance),
            "evidence_refs": list(self.evidence_refs),
            "generated_at": self.generated_at,
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "readiness_summary": (
                self.readiness_summary.to_json_dict()
                if self.readiness_summary is not None
                else None
            ),
            "blocking_summary": (
                self.blocking_summary.to_json_dict()
                if self.blocking_summary is not None
                else None
            ),
            "action_queue_summary": (
                self.action_queue_summary.to_json_dict()
                if self.action_queue_summary is not None
                else None
            ),
            "review_required_summary": (
                self.review_required_summary.to_json_dict()
                if self.review_required_summary is not None
                else None
            ),
        }


@dataclass
class _ObservedProviderStats:
    """Internal aggregation row for provider health derivation."""

    provider_names: set[str] = field(default_factory=set)
    sample_count: int = 0
    failure_count: int = 0


@dataclass(frozen=True)
class OperationalReadinessReport:
    """Canonical Sprint-21 readiness report.

    Read-only by design: no execution, no write-back, no auto-remediation,
    no routing changes, and no DB mutation.
    """

    readiness_status: str
    highest_severity: str
    issue_count: int
    collector_summary: HandoffCollectorSummaryReport
    route_summary: RouteReadinessSummary
    alert_dispatch_summary: AlertDispatchSummary
    provider_health_summary: ProviderHealthSummary
    distribution_drift_summary: DistributionDriftSummary
    issues: list[ReadinessIssue]
    protective_gate_summary: ProtectiveGateSummary = field(
        default_factory=ProtectiveGateSummary
    )
    artifacts: OperationalArtifactRefs = field(default_factory=OperationalArtifactRefs)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "operational_readiness",
            "generated_at": self.generated_at,
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "readiness_status": self.readiness_status,
            "highest_severity": self.highest_severity,
            "issue_count": self.issue_count,
            "collector_summary": self.collector_summary.to_json_dict(),
            "route_summary": self.route_summary.to_json_dict(),
            "alert_dispatch_summary": self.alert_dispatch_summary.to_json_dict(),
            "provider_health_summary": self.provider_health_summary.to_json_dict(),
            "distribution_drift_summary": self.distribution_drift_summary.to_json_dict(),
            "protective_gate_summary": self.protective_gate_summary.to_json_dict(),
            "issues": [issue.to_json_dict() for issue in self.issues],
            "artifacts": self.artifacts.to_json_dict(),
        }


def _build_alert_dispatch_summary(
    alert_audits: list[AlertAuditRecord],
) -> AlertDispatchSummary:
    by_channel: dict[str, int] = {}
    digest_count = 0
    latest_dispatched_at: str | None = None
    latest_timestamp: datetime | None = None

    for audit in alert_audits:
        by_channel[audit.channel] = by_channel.get(audit.channel, 0) + 1
        if audit.is_digest:
            digest_count += 1
        dispatched_at = _parse_iso_timestamp(audit.dispatched_at)
        if dispatched_at is not None and (
            latest_timestamp is None or dispatched_at > latest_timestamp
        ):
            latest_timestamp = dispatched_at
            latest_dispatched_at = audit.dispatched_at

    return AlertDispatchSummary(
        total_count=len(alert_audits),
        digest_count=digest_count,
        by_channel=by_channel,
        latest_dispatched_at=latest_dispatched_at,
    )


def _default_provider_name(path_id: str) -> str:
    normalized = path_id.strip()
    if "." in normalized:
        suffix = normalized.split(".", 1)[1].strip()
        if suffix:
            return suffix
    return "unknown"


def _record_provider_observation(
    stats_by_path: dict[str, _ObservedProviderStats],
    *,
    path_id: str,
    provider: str | None,
    failed: bool,
) -> None:
    stats = stats_by_path.setdefault(path_id, _ObservedProviderStats())
    stats.provider_names.add((provider or _default_provider_name(path_id)).strip())
    stats.sample_count += 1
    if failed:
        stats.failure_count += 1


def _build_provider_health_summary(
    *,
    handoffs: list[SignalHandoff],
    active_route_state: ActiveRouteState | None,
    envelopes: list[ABCInferenceEnvelope],
) -> ProviderHealthSummary:
    stats_by_path: dict[str, _ObservedProviderStats] = {}

    for handoff in handoffs:
        _record_provider_observation(
            stats_by_path,
            path_id=handoff.route_path,
            provider=handoff.provider,
            failed=False,
        )

    include_primary_from_envelopes = not handoffs
    for envelope in envelopes:
        if include_primary_from_envelopes:
            _record_provider_observation(
                stats_by_path,
                path_id=envelope.primary_result.path_id,
                provider=envelope.primary_result.provider,
                failed=_path_failed(envelope.primary_result),
            )

        for shadow_path in envelope.shadow_results:
            _record_provider_observation(
                stats_by_path,
                path_id=shadow_path.path_id,
                provider=shadow_path.provider,
                failed=_path_failed(shadow_path),
            )

        if envelope.control_result is not None:
            _record_provider_observation(
                stats_by_path,
                path_id=envelope.control_result.path_id,
                provider=envelope.control_result.provider,
                failed=_path_failed(envelope.control_result),
            )

    expected_paths: list[str] = []
    if active_route_state is not None:
        expected_paths.append(active_route_state.active_primary_path)
        expected_paths.extend(active_route_state.enabled_shadow_paths)
        if active_route_state.control_path is not None:
            expected_paths.append(active_route_state.control_path)
    else:
        expected_paths.extend(sorted(stats_by_path))

    entries: list[ProviderHealthEntry] = []
    seen_paths: set[str] = set()

    for path_id in expected_paths:
        seen_paths.add(path_id)
        classification = classify_delivery_for_route(path_id)
        stats = stats_by_path.get(path_id, _ObservedProviderStats())
        sample_count = stats.sample_count
        failure_count = stats.failure_count
        success_count = max(0, sample_count - failure_count)
        status = (
            PROVIDER_STATUS_UNAVAILABLE
            if sample_count == 0
            else (
                PROVIDER_STATUS_DEGRADED
                if failure_count > 0
                else PROVIDER_STATUS_HEALTHY
            )
        )
        provider = (
            ", ".join(sorted(name for name in stats.provider_names if name))
            or _default_provider_name(path_id)
        )
        entries.append(
            ProviderHealthEntry(
                provider=provider,
                path_id=path_id,
                path_type=classification.path_type,
                status=status,
                sample_count=sample_count,
                success_count=success_count,
                failure_count=failure_count,
                expected=True,
            )
        )

    for path_id in sorted(path for path in stats_by_path if path not in seen_paths):
        classification = classify_delivery_for_route(path_id)
        stats = stats_by_path[path_id]
        failure_count = stats.failure_count
        sample_count = stats.sample_count
        entries.append(
            ProviderHealthEntry(
                provider=", ".join(sorted(name for name in stats.provider_names if name))
                or _default_provider_name(path_id),
                path_id=path_id,
                path_type=classification.path_type,
                status=(
                    PROVIDER_STATUS_DEGRADED
                    if failure_count > 0
                    else PROVIDER_STATUS_HEALTHY
                ),
                sample_count=sample_count,
                success_count=max(0, sample_count - failure_count),
                failure_count=failure_count,
                expected=False,
            )
        )

    healthy_count = sum(1 for entry in entries if entry.status == PROVIDER_STATUS_HEALTHY)
    degraded_count = sum(
        1 for entry in entries if entry.status == PROVIDER_STATUS_DEGRADED
    )
    unavailable_count = sum(
        1 for entry in entries if entry.status == PROVIDER_STATUS_UNAVAILABLE
    )
    provider_names = {
        entry.provider
        for entry in entries
        if entry.provider.strip() and entry.provider != "unknown"
    }

    return ProviderHealthSummary(
        entries=entries,
        provider_count=len(provider_names),
        healthy_count=healthy_count,
        degraded_count=degraded_count,
        unavailable_count=unavailable_count,
    )


def _build_distribution_drift_summary(
    *,
    handoffs: list[SignalHandoff],
    active_route_state: ActiveRouteState | None,
    envelopes: list[ABCInferenceEnvelope],
) -> DistributionDriftSummary:
    production_handoff_count = 0
    shadow_audit_result_count = 0
    control_comparison_result_count = 0
    classification_mismatch_count = 0
    visibility_mismatch_count = 0
    unexpected_visible_audit_count = 0
    unknown_paths: set[str] = set()
    observed_shadow_paths: set[str] = set()
    observed_control_paths: set[str] = set()

    for handoff in handoffs:
        canonical = classify_delivery_for_route(handoff.route_path)
        if canonical.path_type == "unknown":
            unknown_paths.add(handoff.route_path)
        if canonical.path_type == "primary" and handoff.consumer_visibility == "visible":
            production_handoff_count += 1
        if (
            handoff.path_type != canonical.path_type
            or handoff.delivery_class != canonical.delivery_class
        ):
            classification_mismatch_count += 1
        if (
            handoff.consumer_visibility != canonical.consumer_visibility
            or handoff.audit_visibility != canonical.audit_visibility
        ):
            visibility_mismatch_count += 1
        if canonical.path_type != "primary" and handoff.consumer_visibility == "visible":
            unexpected_visible_audit_count += 1

    for envelope in envelopes:
        canonical_primary = classify_delivery_for_route(envelope.primary_result.path_id)
        if canonical_primary.path_type == "unknown":
            unknown_paths.add(envelope.primary_result.path_id)

        for shadow_path in envelope.shadow_results:
            canonical = classify_delivery_for_route(shadow_path.path_id)
            if canonical.path_type == "unknown":
                unknown_paths.add(shadow_path.path_id)
            elif canonical.path_type == "shadow":
                shadow_audit_result_count += 1
                observed_shadow_paths.add(shadow_path.path_id)

        if envelope.control_result is not None:
            canonical = classify_delivery_for_route(envelope.control_result.path_id)
            if canonical.path_type == "unknown":
                unknown_paths.add(envelope.control_result.path_id)
            elif canonical.path_type == "control":
                control_comparison_result_count += 1
                observed_control_paths.add(envelope.control_result.path_id)

    expected_shadow_path_count = (
        len(active_route_state.enabled_shadow_paths)
        if active_route_state is not None
        else 0
    )
    expected_control_count = (
        1
        if active_route_state is not None and active_route_state.control_path is not None
        else 0
    )
    observed_shadow_path_count = len(observed_shadow_paths)
    observed_control_count = len(observed_control_paths)

    status = DRIFT_STATUS_NOMINAL
    if (
        classification_mismatch_count > 0
        or visibility_mismatch_count > 0
        or unexpected_visible_audit_count > 0
    ):
        status = DRIFT_STATUS_CRITICAL
    elif (
        unknown_paths
        or observed_shadow_path_count < expected_shadow_path_count
        or observed_control_count < expected_control_count
    ):
        status = DRIFT_STATUS_WARNING

    return DistributionDriftSummary(
        status=status,
        production_handoff_count=production_handoff_count,
        shadow_audit_result_count=shadow_audit_result_count,
        control_comparison_result_count=control_comparison_result_count,
        expected_shadow_path_count=expected_shadow_path_count,
        observed_shadow_path_count=observed_shadow_path_count,
        expected_control_count=expected_control_count,
        observed_control_count=observed_control_count,
        unknown_path_count=len(unknown_paths),
        classification_mismatch_count=classification_mismatch_count,
        visibility_mismatch_count=visibility_mismatch_count,
        unexpected_visible_audit_count=unexpected_visible_audit_count,
    )


def _build_route_summary(
    *,
    active_route_state: ActiveRouteState | None,
    envelopes: list[ABCInferenceEnvelope],
    artifacts: OperationalArtifactRefs,
    now: datetime,
) -> RouteReadinessSummary:
    if active_route_state is None:
        return RouteReadinessSummary(
            active=False,
            abc_output_expected=artifacts.abc_output.path,
            abc_output_available=artifacts.abc_output.present,
        )

    seen_shadow_paths = {
        path.path_id
        for envelope in envelopes
        for path in envelope.shadow_results
    }
    control_results = [
        envelope.control_result
        for envelope in envelopes
        if envelope.control_result is not None
    ]
    shadow_failure_count = sum(
        1
        for envelope in envelopes
        for path in envelope.shadow_results
        if _path_failed(path)
    )
    control_failure_count = sum(1 for path in control_results if _path_failed(path))
    missing_shadow_paths = (
        sorted(
            path_id
            for path_id in active_route_state.enabled_shadow_paths
            if path_id not in seen_shadow_paths
        )
        if envelopes
        else []
    )
    missing_control_result = bool(
        active_route_state.control_path is not None and envelopes and not control_results
    )

    return RouteReadinessSummary(
        active=True,
        route_profile=active_route_state.route_profile,
        active_primary_path=active_route_state.active_primary_path,
        abc_output_expected=artifacts.abc_output.path or active_route_state.abc_envelope_output,
        abc_output_available=artifacts.abc_output.present,
        shadow_paths_configured=list(active_route_state.enabled_shadow_paths),
        control_path=active_route_state.control_path,
        shadow_failure_count=shadow_failure_count,
        control_failure_count=control_failure_count,
        missing_shadow_paths=missing_shadow_paths,
        missing_control_result=missing_control_result,
        state_age_hours=_age_hours(
            earlier=_parse_iso_timestamp(active_route_state.activated_at),
            now=now,
        ),
    )


def _build_stale_pending_count(
    *,
    handoffs: list[SignalHandoff],
    collector_summary: HandoffCollectorSummaryReport,
    stale_after_hours: int,
    now: datetime,
) -> int:
    pending_ids = {entry.handoff_id for entry in collector_summary.pending_handoffs}
    stale_pending = 0
    for handoff in handoffs:
        if handoff.handoff_id not in pending_ids:
            continue
        handoff_age_hours = _age_hours(
            earlier=_parse_iso_timestamp(handoff.handoff_at),
            now=now,
        )
        if handoff_age_hours is not None and handoff_age_hours >= stale_after_hours:
            stale_pending += 1
    return stale_pending


def _highest_severity(issues: list[ReadinessIssue]) -> str:
    if not issues:
        return "none"
    return max(issues, key=lambda issue: _SEVERITY_ORDER[issue.severity]).severity


def _gate_status_for_issue(issue: ReadinessIssue) -> str:
    if issue.severity == SEVERITY_CRITICAL:
        return GATE_STATUS_BLOCKING
    if issue.category == CATEGORY_ACKNOWLEDGEMENT_AUDIT:
        return GATE_STATUS_ADVISORY
    if issue.severity == SEVERITY_WARNING:
        return GATE_STATUS_WARNING
    return GATE_STATUS_ADVISORY


def _subsystem_for_issue(issue: ReadinessIssue) -> str:
    if issue.category in {CATEGORY_HANDOFF_BACKLOG, CATEGORY_ACKNOWLEDGEMENT_AUDIT}:
        return "handoff"
    if issue.category == CATEGORY_STALE_STATE:
        source_ref = (issue.source_ref or "").replace("\\", "/").lower()
        return "routing" if "active_route" in source_ref else "handoff"
    if issue.category in {
        CATEGORY_PROVIDER_HEALTH,
        CATEGORY_ROUTE_PROVIDER,
        CATEGORY_SHADOW_CONTROL_FAILURE,
    }:
        return "providers"
    if issue.category == CATEGORY_DISTRIBUTION_DRIFT:
        return "distribution"
    if issue.category == CATEGORY_ARTIFACT_STATE:
        return "artifacts"
    return "readiness"


def _recommended_actions_for_issue(issue: ReadinessIssue) -> list[str]:
    if issue.category == CATEGORY_HANDOFF_BACKLOG:
        return [
            "Review pending handoffs and confirm whether consumer acknowledgement is stalled.",
            "Acknowledge or retire stale handoffs only through the append-only audit trail.",
        ]
    if issue.category == CATEGORY_ACKNOWLEDGEMENT_AUDIT:
        return [
            "Inspect orphaned acknowledgement rows against the current handoff artifact.",
            "Correct the handoff reference before accepting further acknowledgements.",
        ]
    if issue.category == CATEGORY_ARTIFACT_STATE:
        return [
            "Restore or regenerate the missing artifact from canonical readiness inputs.",
            "Verify the configured artifact path still points to the intended workspace file.",
        ]
    if issue.category == CATEGORY_PROVIDER_HEALTH:
        return [
            "Inspect degraded or unavailable provider paths in the ABC/readiness artifacts.",
            "Keep productive delivery on the current primary path until provider health recovers.",
        ]
    if issue.category == CATEGORY_ROUTE_PROVIDER:
        return [
            (
                "Review the active route profile against the ABC audit output "
                "for missing path coverage."
            ),
            "Re-run the controlled route only after the expected provider paths are present.",
        ]
    if issue.category == CATEGORY_DISTRIBUTION_DRIFT:
        return [
            "Review route-aware handoff classification and visibility metadata for mismatches.",
            "Keep external consumers on canonical primary-only handoffs until drift is resolved.",
        ]
    if issue.category == CATEGORY_SHADOW_CONTROL_FAILURE:
        return [
            "Inspect shadow/control failures in the ABC audit output before comparing providers.",
            "Treat shadow/control results as audit-only until the failure condition is cleared.",
        ]
    if issue.category == CATEGORY_STALE_STATE:
        return [
            (
                "Refresh the stale readiness artifact and verify its timestamp "
                "against the current run."
            ),
            (
                "Avoid operator decisions based on stale backlog or route "
                "state until the artifact is current."
            ),
        ]
    return [
        "Review the readiness issue in its source artifact before taking operator action.",
    ]


def _evidence_refs_for_issue(issue: ReadinessIssue) -> list[str]:
    refs: list[str] = []
    for value in (issue.source_ref, issue.path_id, issue.provider):
        if value and value not in refs:
            refs.append(value)
    return refs


def _build_protective_gate_summary(
    issues: list[ReadinessIssue],
) -> ProtectiveGateSummary:
    items: list[ProtectiveGateItem] = []
    blocking_count = 0
    warning_count = 0
    advisory_count = 0

    for issue in issues:
        gate_status = _gate_status_for_issue(issue)
        if gate_status == GATE_STATUS_BLOCKING:
            blocking_count += 1
        elif gate_status == GATE_STATUS_WARNING:
            warning_count += 1
        else:
            advisory_count += 1

        items.append(
            ProtectiveGateItem(
                gate_status=gate_status,
                severity=issue.severity,
                category=issue.category,
                summary=issue.summary,
                subsystem=_subsystem_for_issue(issue),
                blocking_reason=issue.summary if gate_status == GATE_STATUS_BLOCKING else None,
                recommended_actions=_recommended_actions_for_issue(issue),
                evidence_refs=_evidence_refs_for_issue(issue),
            )
        )

    summary_status = GATE_STATUS_CLEAR
    if blocking_count > 0:
        summary_status = GATE_STATUS_BLOCKING
    elif warning_count > 0:
        summary_status = GATE_STATUS_WARNING
    elif advisory_count > 0:
        summary_status = GATE_STATUS_ADVISORY

    return ProtectiveGateSummary(
        gate_status=summary_status,
        blocking_count=blocking_count,
        warning_count=warning_count,
        advisory_count=advisory_count,
        items=items,
    )




def build_operational_readiness_report(
    *,
    handoffs: list[SignalHandoff],
    collector_summary: HandoffCollectorSummaryReport,
    alert_audits: list[AlertAuditRecord] | None = None,
    active_route_state: ActiveRouteState | None = None,
    envelopes: list[ABCInferenceEnvelope] | None = None,
    artifacts: OperationalArtifactRefs | None = None,
    stale_after_hours: int = 24,
    now: datetime | None = None,
) -> OperationalReadinessReport:
    """Build a canonical read-only readiness report from existing artifacts only."""
    if stale_after_hours < 1:
        raise ValueError("stale_after_hours must be >= 1")

    current_time = now or datetime.now(UTC)
    resolved_artifacts = artifacts or OperationalArtifactRefs()
    resolved_alerts = list(alert_audits or [])
    resolved_envelopes = list(envelopes or [])

    route_summary = _build_route_summary(
        active_route_state=active_route_state,
        envelopes=resolved_envelopes,
        artifacts=resolved_artifacts,
        now=current_time,
    )
    alert_summary = _build_alert_dispatch_summary(resolved_alerts)
    provider_health_summary = _build_provider_health_summary(
        handoffs=handoffs,
        active_route_state=active_route_state,
        envelopes=resolved_envelopes,
    )
    distribution_drift_summary = _build_distribution_drift_summary(
        handoffs=handoffs,
        active_route_state=active_route_state,
        envelopes=resolved_envelopes,
    )

    issues: list[ReadinessIssue] = []

    if resolved_artifacts.handoff.path is not None and not resolved_artifacts.handoff.present:
        issues.append(
            ReadinessIssue(
                severity=SEVERITY_WARNING,
                category=CATEGORY_ARTIFACT_STATE,
                summary="Configured handoff artifact is missing.",
                source_ref=resolved_artifacts.handoff.path,
            )
        )

    if collector_summary.pending_count > 0:
        issues.append(
            ReadinessIssue(
                severity=SEVERITY_WARNING,
                category=CATEGORY_HANDOFF_BACKLOG,
                summary=(
                    f"{collector_summary.pending_count} handoff(s) are still pending "
                    "consumer acknowledgement."
                ),
                source_ref=resolved_artifacts.handoff.path,
            )
        )

    stale_pending_count = _build_stale_pending_count(
        handoffs=handoffs,
        collector_summary=collector_summary,
        stale_after_hours=stale_after_hours,
        now=current_time,
    )
    if stale_pending_count > 0:
        issues.append(
            ReadinessIssue(
                severity=SEVERITY_WARNING,
                category=CATEGORY_STALE_STATE,
                summary=(
                    f"{stale_pending_count} pending handoff(s) are older than "
                    f"{stale_after_hours}h."
                ),
                source_ref=resolved_artifacts.handoff.path,
            )
        )

    if collector_summary.orphaned_ack_count > 0:
        issues.append(
            ReadinessIssue(
                severity=SEVERITY_WARNING,
                category=CATEGORY_ACKNOWLEDGEMENT_AUDIT,
                summary=(
                    f"{collector_summary.orphaned_ack_count} acknowledgement row(s) "
                    "reference unknown handoff IDs."
                ),
                source_ref=resolved_artifacts.acknowledgements.path,
            )
        )

    if (
        provider_health_summary.degraded_count > 0
        or provider_health_summary.unavailable_count > 0
    ):
        issues.append(
            ReadinessIssue(
                severity=SEVERITY_WARNING,
                category=CATEGORY_PROVIDER_HEALTH,
                summary=(
                    "Provider health requires attention "
                    f"(degraded={provider_health_summary.degraded_count}, "
                    f"unavailable={provider_health_summary.unavailable_count})."
                ),
                source_ref=(
                    resolved_artifacts.abc_output.path
                    or resolved_artifacts.handoff.path
                    or resolved_artifacts.active_route_state.path
                ),
            )
        )

    if (
        route_summary.active
        and route_summary.abc_output_expected
        and not route_summary.abc_output_available
    ):
        issues.append(
            ReadinessIssue(
                severity=SEVERITY_CRITICAL,
                category=CATEGORY_ARTIFACT_STATE,
                summary="Active route expects an ABC envelope artifact, but none is available.",
                source_ref=route_summary.abc_output_expected,
            )
        )

    if distribution_drift_summary.status != DRIFT_STATUS_NOMINAL:
        issues.append(
            ReadinessIssue(
                severity=(
                    SEVERITY_CRITICAL
                    if distribution_drift_summary.status == DRIFT_STATUS_CRITICAL
                    else SEVERITY_WARNING
                ),
                category=CATEGORY_DISTRIBUTION_DRIFT,
                summary=(
                    "Distribution drift detected "
                    f"(classification_mismatches="
                    f"{distribution_drift_summary.classification_mismatch_count}, "
                    f"visibility_mismatches="
                    f"{distribution_drift_summary.visibility_mismatch_count}, "
                    f"unknown_paths={distribution_drift_summary.unknown_path_count}, "
                    f"unexpected_visible_audit="
                    f"{distribution_drift_summary.unexpected_visible_audit_count})."
                ),
                source_ref=(
                    resolved_artifacts.handoff.path
                    or resolved_artifacts.abc_output.path
                ),
            )
        )

    if route_summary.active and route_summary.state_age_hours is not None:
        if route_summary.state_age_hours >= stale_after_hours:
            issues.append(
                ReadinessIssue(
                    severity=SEVERITY_WARNING,
                    category=CATEGORY_STALE_STATE,
                    summary=(
                        f"Active route state is {route_summary.state_age_hours:.1f}h old."
                    ),
                    source_ref=resolved_artifacts.active_route_state.path,
                )
            )

    if route_summary.missing_shadow_paths:
        issues.append(
            ReadinessIssue(
                severity=SEVERITY_WARNING,
                category=CATEGORY_ROUTE_PROVIDER,
                summary=(
                    "Configured shadow paths are missing from the ABC audit output: "
                    + ", ".join(route_summary.missing_shadow_paths)
                ),
                source_ref=resolved_artifacts.abc_output.path,
            )
        )

    if route_summary.missing_control_result:
        issues.append(
            ReadinessIssue(
                severity=SEVERITY_WARNING,
                category=CATEGORY_ROUTE_PROVIDER,
                summary="Configured control path has no control result in the ABC audit output.",
                source_ref=resolved_artifacts.abc_output.path,
                path_id=route_summary.control_path,
            )
        )

    if route_summary.shadow_failure_count or route_summary.control_failure_count:
        issues.append(
            ReadinessIssue(
                severity=SEVERITY_WARNING,
                category=CATEGORY_SHADOW_CONTROL_FAILURE,
                summary=(
                    "Shadow/control failures are visible in the ABC audit output "
                    f"(shadow={route_summary.shadow_failure_count}, "
                    f"control={route_summary.control_failure_count})."
                ),
                source_ref=resolved_artifacts.abc_output.path,
            )
        )

    highest_severity = _highest_severity(issues)
    readiness_status = "ready" if highest_severity == "none" else highest_severity
    protective_gate_summary = _build_protective_gate_summary(issues)

    return OperationalReadinessReport(
        readiness_status=readiness_status,
        highest_severity=highest_severity,
        issue_count=len(issues),
        collector_summary=collector_summary,
        route_summary=route_summary,
        alert_dispatch_summary=alert_summary,
        provider_health_summary=provider_health_summary,
        distribution_drift_summary=distribution_drift_summary,
        protective_gate_summary=protective_gate_summary,
        issues=issues,
        artifacts=resolved_artifacts,
    )


def save_operational_readiness_report(
    report: OperationalReadinessReport,
    output_path: Path | str,
) -> Path:
    """Persist an operational readiness report as structured JSON."""
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(report.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved


def _unique_strings(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return unique


def _highest_escalation_severity(items: list[OperationalEscalationItem]) -> str:
    if not items:
        return "none"
    return max(items, key=lambda item: _SEVERITY_ORDER[item.severity]).severity


def _summary_status_from_items(items: list[OperationalEscalationItem]) -> str:
    if any(item.blocking for item in items):
        return GATE_STATUS_BLOCKING
    if any(item.escalation_status == GATE_STATUS_WARNING for item in items):
        return GATE_STATUS_WARNING
    if any(item.escalation_status == GATE_STATUS_ADVISORY for item in items):
        return GATE_STATUS_ADVISORY
    return GATE_STATUS_CLEAR


def _build_gate_escalation_items(
    gate_summary: ProtectiveGateSummary,
) -> list[OperationalEscalationItem]:
    items: list[OperationalEscalationItem] = []
    for gate_item in gate_summary.items:
        items.append(
            OperationalEscalationItem(
                escalation_status=gate_item.gate_status,
                severity=gate_item.severity,
                blocking=gate_item.gate_status == GATE_STATUS_BLOCKING,
                category=gate_item.category,
                subsystem=gate_item.subsystem,
                summary=gate_item.summary,
                operator_action_required=gate_item.gate_status
                in {GATE_STATUS_BLOCKING, GATE_STATUS_WARNING},
                blocking_reason=gate_item.blocking_reason,
                evidence_refs=list(gate_item.evidence_refs),
                advisory_notes=list(gate_item.recommended_actions),
            )
        )
    return items


def _build_review_required_escalation_items(
    review_required_summary: ReviewRequiredArtifactSummary | None,
) -> list[OperationalEscalationItem]:
    if review_required_summary is None:
        return []

    items: list[OperationalEscalationItem] = []
    for entry in review_required_summary.entries:
        items.append(
            OperationalEscalationItem(
                escalation_status=GATE_STATUS_WARNING,
                severity=SEVERITY_WARNING,
                blocking=False,
                category=CATEGORY_REVIEW_REQUIRED,
                subsystem="artifacts",
                summary=f"Artifact requires operator review before archival: {entry.path}",
                operator_action_required=True,
                evidence_refs=_unique_strings([entry.path, entry.artifact_class]),
                advisory_notes=_unique_strings(
                    [entry.retention_rationale, entry.operator_guidance]
                ),
            )
        )
    return items


def build_operational_escalation_summary(
    readiness_report: OperationalReadinessReport,
    *,
    review_required_summary: ReviewRequiredArtifactSummary | None = None,
) -> OperationalEscalationSummary:
    """Project a safe, read-only escalation surface from canonical reports only."""
    items = _build_gate_escalation_items(
        readiness_report.protective_gate_summary
    ) + _build_review_required_escalation_items(review_required_summary)
    blocking_count = sum(1 for item in items if item.blocking)
    warning_count = sum(
        1 for item in items if item.escalation_status == GATE_STATUS_WARNING
    )
    advisory_count = sum(
        1 for item in items if item.escalation_status == GATE_STATUS_ADVISORY
    )
    review_required_count = sum(
        1 for item in items if item.category == CATEGORY_REVIEW_REQUIRED
    )
    operator_action_count = sum(1 for item in items if item.operator_action_required)

    return OperationalEscalationSummary(
        escalation_status=_summary_status_from_items(items),
        severity=_highest_escalation_severity(items),
        blocking=blocking_count > 0,
        blocking_count=blocking_count,
        warning_count=warning_count,
        advisory_count=advisory_count,
        review_required_count=review_required_count,
        operator_action_count=operator_action_count,
        items=items,
        evidence_refs=_unique_strings(
            [ref for item in items for ref in item.evidence_refs]
        ),
        advisory_notes=_unique_strings(
            [note for item in items for note in item.advisory_notes]
        ),
        generated_at=readiness_report.generated_at,
    )


def build_blocking_summary(summary: OperationalEscalationSummary) -> BlockingSummary:
    """Project blocking escalation items only from the canonical escalation summary."""
    items = [item for item in summary.items if item.blocking]
    return BlockingSummary(
        escalation_status=GATE_STATUS_BLOCKING if items else GATE_STATUS_CLEAR,
        severity=SEVERITY_CRITICAL if items else "none",
        blocking=bool(items),
        blocking_count=len(items),
        items=items,
        evidence_refs=_unique_strings([ref for item in items for ref in item.evidence_refs]),
        advisory_notes=_unique_strings(
            [note for item in items for note in item.advisory_notes]
        ),
    )


def _action_queue_status_for_item(item: OperationalEscalationItem) -> str:
    if item.blocking:
        return ACTION_QUEUE_STATUS_BLOCKING
    if item.category == CATEGORY_REVIEW_REQUIRED:
        return ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    return ACTION_QUEUE_STATUS_OPEN


def _action_priority_for_item(item: OperationalEscalationItem) -> str:
    if item.blocking or item.severity == SEVERITY_CRITICAL:
        return ACTION_PRIORITY_P1
    if item.category == CATEGORY_REVIEW_REQUIRED or item.severity == SEVERITY_WARNING:
        return ACTION_PRIORITY_P2
    return ACTION_PRIORITY_P3


def _build_action_id(item: OperationalEscalationItem) -> str:
    digest = hashlib.sha1(
        "|".join(
            [
                item.category,
                item.subsystem,
                item.summary,
                item.blocking_reason or "",
                ",".join(item.evidence_refs),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"act_{digest[:12]}"


def _build_action_queue_items(
    items: list[OperationalEscalationItem],
) -> list[ActionQueueItem]:
    queue_items = [
        ActionQueueItem(
            action_id=_build_action_id(item),
            severity=item.severity,
            priority=_action_priority_for_item(item),
            queue_status=_action_queue_status_for_item(item),
            category=item.category,
            subsystem=item.subsystem,
            summary=item.summary,
            operator_action_required=item.operator_action_required,
            blocking=item.blocking,
            blocking_reason=item.blocking_reason,
            evidence_refs=list(item.evidence_refs),
            advisory_notes=list(item.advisory_notes),
        )
        for item in items
    ]
    queue_items.sort(
        key=lambda item: (
            _ACTION_PRIORITY_ORDER[item.priority],
            _ACTION_QUEUE_STATUS_ORDER[item.queue_status],
            -_SEVERITY_ORDER[item.severity],
            item.summary.lower(),
        )
    )
    return queue_items


def _queue_status_from_action_items(items: list[ActionQueueItem]) -> str:
    if any(item.queue_status == ACTION_QUEUE_STATUS_BLOCKING for item in items):
        return ACTION_QUEUE_STATUS_BLOCKING
    if any(item.queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED for item in items):
        return ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    if items:
        return ACTION_QUEUE_STATUS_OPEN
    return ACTION_QUEUE_STATUS_CLEAR


def _highest_action_priority(items: list[ActionQueueItem]) -> str:
    if not items:
        return "none"
    return min(items, key=lambda item: _ACTION_PRIORITY_ORDER[item.priority]).priority


def _operator_decision_pack_status(
    *,
    readiness_summary: OperationalReadinessReport | None,
    blocking_summary: BlockingSummary | None,
    action_queue_summary: ActionQueueSummary | None,
    review_required_summary: ReviewRequiredArtifactSummary | None,
) -> str:
    if blocking_summary is not None and blocking_summary.blocking_count > 0:
        return GATE_STATUS_BLOCKING
    if (
        action_queue_summary is not None
        and action_queue_summary.queue_status != ACTION_QUEUE_STATUS_CLEAR
    ):
        return action_queue_summary.queue_status
    if (
        review_required_summary is not None
        and review_required_summary.review_required_count > 0
    ):
        return ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    if readiness_summary is not None:
        gate_status = readiness_summary.protective_gate_summary.gate_status
        if gate_status != GATE_STATUS_CLEAR:
            return gate_status
        if readiness_summary.readiness_status == "ready":
            return GATE_STATUS_CLEAR
        return readiness_summary.readiness_status
    return GATE_STATUS_CLEAR


def _readiness_pack_evidence_refs(
    readiness_summary: OperationalReadinessReport | None,
) -> list[str]:
    if readiness_summary is None:
        return []
    return _unique_strings(
        [
            ref
            for item in readiness_summary.protective_gate_summary.items
            for ref in item.evidence_refs
        ]
        + [
            issue.source_ref
            for issue in readiness_summary.issues
            if issue.source_ref is not None
        ]
    )


def _readiness_pack_guidance(
    readiness_summary: OperationalReadinessReport | None,
) -> list[str]:
    if readiness_summary is None:
        return []
    return _unique_strings(
        [
            action
            for item in readiness_summary.protective_gate_summary.items
            for action in item.recommended_actions
        ]
    )


def _pack_review_required_evidence_refs(
    review_required_summary: ReviewRequiredArtifactSummary | None,
) -> list[str]:
    if review_required_summary is None:
        return []
    return _unique_strings(
        [
            ref
            for entry in review_required_summary.entries
            for ref in (entry.path, entry.artifact_class)
        ]
    )


def _pack_review_required_guidance(
    review_required_summary: ReviewRequiredArtifactSummary | None,
) -> list[str]:
    if review_required_summary is None:
        return []
    return _unique_strings(
        [
            note
            for entry in review_required_summary.entries
            for note in (entry.retention_rationale, entry.operator_guidance)
        ]
    )


def _pack_affected_subsystems(
    *,
    readiness_summary: OperationalReadinessReport | None,
    blocking_summary: BlockingSummary | None,
    action_queue_summary: ActionQueueSummary | None,
    review_required_summary: ReviewRequiredArtifactSummary | None,
) -> list[str]:
    subsystems: list[str] = []
    if blocking_summary is not None:
        subsystems.extend(item.subsystem for item in blocking_summary.items)
    if action_queue_summary is not None:
        subsystems.extend(item.subsystem for item in action_queue_summary.items)
    if readiness_summary is not None:
        subsystems.extend(
            item.subsystem for item in readiness_summary.protective_gate_summary.items
        )
    if (
        review_required_summary is not None
        and review_required_summary.review_required_count > 0
    ):
        subsystems.append("artifacts")
    return _unique_strings(subsystems)


def build_action_queue_summary(
    summary: OperationalEscalationSummary,
) -> ActionQueueSummary:
    """Project the canonical operator action queue from escalation only."""
    action_items = [item for item in summary.items if item.operator_action_required]
    queue_items = _build_action_queue_items(action_items)
    return ActionQueueSummary(
        queue_status=_queue_status_from_action_items(queue_items),
        total_count=len(queue_items),
        open_count=sum(
            1 for item in queue_items if item.queue_status == ACTION_QUEUE_STATUS_OPEN
        ),
        blocking_count=sum(
            1 for item in queue_items if item.queue_status == ACTION_QUEUE_STATUS_BLOCKING
        ),
        review_required_count=sum(
            1
            for item in queue_items
            if item.queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
        ),
        highest_priority=_highest_action_priority(queue_items),
        items=queue_items,
        evidence_refs=_unique_strings(
            [ref for item in queue_items for ref in item.evidence_refs]
        ),
        advisory_notes=_unique_strings(
            [note for item in queue_items for note in item.advisory_notes]
        ),
        generated_at=summary.generated_at,
    )


def build_operator_action_summary(
    summary: OperationalEscalationSummary,
) -> OperatorActionSummary:
    """Project operator-action-required escalation rows only."""
    items = [item for item in summary.items if item.operator_action_required]
    return OperatorActionSummary(
        escalation_status=_summary_status_from_items(items),
        severity=_highest_escalation_severity(items),
        blocking=any(item.blocking for item in items),
        operator_action_count=len(items),
        review_required_count=sum(
            1 for item in items if item.category == CATEGORY_REVIEW_REQUIRED
        ),
        items=items,
        evidence_refs=_unique_strings([ref for item in items for ref in item.evidence_refs]),
        advisory_notes=_unique_strings(
            [note for item in items for note in item.advisory_notes]
        ),
    )


def build_blocking_actions(summary: ActionQueueSummary) -> BlockingActionsSummary:
    """Project blocking action queue rows only from the canonical action queue."""
    items = [
        item
        for item in summary.items
        if item.queue_status == ACTION_QUEUE_STATUS_BLOCKING
    ]
    return BlockingActionsSummary(
        queue_status=(
            ACTION_QUEUE_STATUS_BLOCKING if items else ACTION_QUEUE_STATUS_CLEAR
        ),
        blocking_count=len(items),
        highest_priority=_highest_action_priority(items),
        items=items,
        evidence_refs=_unique_strings([ref for item in items for ref in item.evidence_refs]),
        advisory_notes=_unique_strings(
            [note for item in items for note in item.advisory_notes]
        ),
    )


def build_prioritized_actions(
    summary: ActionQueueSummary,
) -> PrioritizedActionsSummary:
    """Project the canonical queue items in derived priority order only."""
    return PrioritizedActionsSummary(
        queue_status=summary.queue_status,
        action_count=summary.total_count,
        highest_priority=summary.highest_priority,
        items=list(summary.items),
        evidence_refs=list(summary.evidence_refs),
        advisory_notes=list(summary.advisory_notes),
    )


def build_review_required_actions(
    summary: ActionQueueSummary,
) -> ReviewRequiredActionsSummary:
    """Project review-required action queue rows only from the canonical queue."""
    items = [
        item
        for item in summary.items
        if item.queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    ]
    return ReviewRequiredActionsSummary(
        queue_status=(
            ACTION_QUEUE_STATUS_REVIEW_REQUIRED
            if items
            else ACTION_QUEUE_STATUS_CLEAR
        ),
        review_required_count=len(items),
        highest_priority=_highest_action_priority(items),
        items=items,
        advisory_notes=_unique_strings(
            [note for item in items for note in item.advisory_notes]
        ),
    )


def build_operator_decision_pack(
    *,
    readiness_summary: OperationalReadinessReport | None = None,
    blocking_summary: BlockingSummary | None = None,
    action_queue_summary: ActionQueueSummary | None = None,
    review_required_summary: ReviewRequiredArtifactSummary | None = None,
) -> OperatorDecisionPack:
    """Assemble the operator decision pack from existing read-only surfaces.

    Pure computation — no I/O, no DB, no LLM, no side-effects.
    Decision pack is a snapshot aggregate; it carries no execution authority.
    I-185–I-192.
    """
    blocking_count = 0
    if blocking_summary is not None:
        blocking_count = blocking_summary.blocking_count
    elif action_queue_summary is not None:
        blocking_count = action_queue_summary.blocking_count

    review_required_count = 0
    if action_queue_summary is not None:
        review_required_count = action_queue_summary.review_required_count
    if review_required_summary is not None:
        review_required_count = max(
            review_required_count,
            review_required_summary.review_required_count,
        )

    action_queue_count = (
        action_queue_summary.total_count if action_queue_summary is not None else 0
    )
    evidence_refs = _unique_strings(
        (blocking_summary.evidence_refs if blocking_summary is not None else [])
        + (action_queue_summary.evidence_refs if action_queue_summary is not None else [])
        + _pack_review_required_evidence_refs(review_required_summary)
        + _readiness_pack_evidence_refs(readiness_summary)
    )
    operator_guidance = _unique_strings(
        (blocking_summary.advisory_notes if blocking_summary is not None else [])
        + (action_queue_summary.advisory_notes if action_queue_summary is not None else [])
        + _pack_review_required_guidance(review_required_summary)
        + _readiness_pack_guidance(readiness_summary)
    )

    generated_at = datetime.now(UTC).isoformat()
    if action_queue_summary is not None:
        generated_at = action_queue_summary.generated_at
    elif readiness_summary is not None:
        generated_at = readiness_summary.generated_at
    elif review_required_summary is not None:
        generated_at = review_required_summary.generated_at

    return OperatorDecisionPack(
        overall_status=_operator_decision_pack_status(
            readiness_summary=readiness_summary,
            blocking_summary=blocking_summary,
            action_queue_summary=action_queue_summary,
            review_required_summary=review_required_summary,
        ),
        blocking_count=blocking_count,
        review_required_count=review_required_count,
        action_queue_count=action_queue_count,
        affected_subsystems=_pack_affected_subsystems(
            readiness_summary=readiness_summary,
            blocking_summary=blocking_summary,
            action_queue_summary=action_queue_summary,
            review_required_summary=review_required_summary,
        ),
        operator_guidance=operator_guidance,
        evidence_refs=evidence_refs,
        readiness_summary=readiness_summary,
        blocking_summary=blocking_summary,
        action_queue_summary=action_queue_summary,
        review_required_summary=review_required_summary,
        generated_at=generated_at,
    )


def save_operational_escalation_summary(
    summary: OperationalEscalationSummary,
    output_path: Path | str,
) -> Path:
    """Persist the operator escalation summary as structured JSON."""
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(summary.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved


# ---------------------------------------------------------------------------
# Sprint 29 — Operator Decision Pack
# ---------------------------------------------------------------------------


def save_operator_decision_pack(
    pack: OperatorDecisionPack,
    output_path: Path | str,
) -> Path:
    """Persist the operator decision pack as structured JSON."""
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(pack.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved


# ---------------------------------------------------------------------------
# Sprint 30 - Operator Runbook
# ---------------------------------------------------------------------------


def _runbook_step_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return f"rbk_{digest[:12]}"


def _runbook_title_for_action(item: ActionQueueItem) -> str:
    if item.queue_status == ACTION_QUEUE_STATUS_BLOCKING:
        return f"Resolve blocking issue in {item.subsystem}"
    if item.queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED:
        return f"Review required artifact in {item.subsystem}"
    return f"Review operator action in {item.subsystem}"


def _runbook_command_refs_for_action(item: ActionQueueItem) -> list[str]:
    if item.queue_status == ACTION_QUEUE_STATUS_BLOCKING:
        return [
            RUNBOOK_COMMAND_BLOCKING_ACTIONS,
            RUNBOOK_COMMAND_BLOCKING_SUMMARY,
            RUNBOOK_COMMAND_DECISION_PACK_SUMMARY,
        ]
    if item.queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED:
        return [
            RUNBOOK_COMMAND_REVIEW_REQUIRED_ACTIONS,
            RUNBOOK_COMMAND_REVIEW_REQUIRED_SUMMARY,
            RUNBOOK_COMMAND_ARTIFACT_RETENTION,
        ]
    return [
        RUNBOOK_COMMAND_PRIORITIZED_ACTIONS,
        RUNBOOK_COMMAND_ACTION_QUEUE_SUMMARY,
        RUNBOOK_COMMAND_DECISION_PACK_SUMMARY,
    ]


@dataclass(frozen=True)
class RunbookStep:
    """Single ordered, read-only operator runbook step."""

    step_id: str
    title: str
    summary: str
    severity: str
    priority: str = ACTION_PRIORITY_P3
    blocking: bool = False
    queue_status: str = ACTION_QUEUE_STATUS_OPEN
    subsystem: str = "unknown"
    operator_action_required: bool = True
    command_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    advisory_notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "summary": self.summary,
            "severity": self.severity,
            "priority": self.priority,
            "blocking": self.blocking,
            "queue_status": self.queue_status,
            "subsystem": self.subsystem,
            "operator_action_required": self.operator_action_required,
            "command_refs": list(self.command_refs),
            "evidence_refs": list(self.evidence_refs),
            "advisory_notes": list(self.advisory_notes),
        }


@dataclass(frozen=True)
class OperatorRunbookSummary:
    """Read-only operator runbook derived from canonical summaries only."""

    overall_status: str = GATE_STATUS_CLEAR
    blocking_count: int = 0
    review_required_count: int = 0
    action_queue_count: int = 0
    affected_subsystems: list[str] = field(default_factory=list)
    operator_guidance: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    command_refs: list[str] = field(default_factory=list)
    steps: list[RunbookStep] = field(default_factory=list)
    next_steps: list[RunbookStep] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    report_type: str = "operator_runbook_summary"
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False
    auto_remediation_enabled: bool = False
    auto_routing_enabled: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": self.report_type,
            "overall_status": self.overall_status,
            "blocking_count": self.blocking_count,
            "review_required_count": self.review_required_count,
            "action_queue_count": self.action_queue_count,
            "affected_subsystems": list(self.affected_subsystems),
            "operator_guidance": list(self.operator_guidance),
            "evidence_refs": list(self.evidence_refs),
            "command_refs": list(self.command_refs),
            "steps": [step.to_json_dict() for step in self.steps],
            "next_steps": [step.to_json_dict() for step in self.next_steps],
            "generated_at": self.generated_at,
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "auto_remediation_enabled": self.auto_remediation_enabled,
            "auto_routing_enabled": self.auto_routing_enabled,
        }


def _runbook_steps_from_action_queue(
    action_queue_summary: ActionQueueSummary | None,
) -> list[RunbookStep]:
    if action_queue_summary is None:
        return []
    return [
        RunbookStep(
            step_id=item.action_id,
            title=_runbook_title_for_action(item),
            summary=item.summary,
            severity=item.severity,
            priority=item.priority,
            blocking=item.blocking,
            queue_status=item.queue_status,
            subsystem=item.subsystem,
            operator_action_required=item.operator_action_required,
            command_refs=_runbook_command_refs_for_action(item),
            evidence_refs=list(item.evidence_refs),
            advisory_notes=list(item.advisory_notes),
        )
        for item in action_queue_summary.items
    ]


def _runbook_fallback_steps(pack: OperatorDecisionPack) -> list[RunbookStep]:
    if pack.review_required_summary is not None and pack.review_required_summary.entries:
        return [
            RunbookStep(
                step_id=_runbook_step_id("review_required", entry.path),
                title="Review retained artifact before any archival decision",
                summary=f"Artifact requires operator review: {entry.path}",
                severity=SEVERITY_WARNING,
                priority=ACTION_PRIORITY_P2,
                blocking=False,
                queue_status=ACTION_QUEUE_STATUS_REVIEW_REQUIRED,
                subsystem="artifacts",
                operator_action_required=True,
                command_refs=[
                    RUNBOOK_COMMAND_REVIEW_REQUIRED_SUMMARY,
                    RUNBOOK_COMMAND_ARTIFACT_RETENTION,
                    RUNBOOK_COMMAND_DECISION_PACK_SUMMARY,
                ],
                evidence_refs=[entry.path, entry.artifact_class],
                advisory_notes=[entry.retention_rationale, entry.operator_guidance],
            )
            for entry in pack.review_required_summary.entries
        ]

    if pack.readiness_summary is not None and pack.readiness_summary.issue_count > 0:
        return [
            RunbookStep(
                step_id=_runbook_step_id(
                    "readiness",
                    str(pack.readiness_summary.issue_count),
                    pack.readiness_summary.highest_severity,
                ),
                title="Review readiness posture",
                summary=(
                    f"Operational readiness reports {pack.readiness_summary.issue_count} "
                    f"issue(s) with highest severity "
                    f"{pack.readiness_summary.highest_severity}."
                ),
                severity=pack.readiness_summary.highest_severity,
                priority=ACTION_PRIORITY_P2,
                blocking=pack.overall_status == GATE_STATUS_BLOCKING,
                queue_status=(
                    ACTION_QUEUE_STATUS_BLOCKING
                    if pack.overall_status == GATE_STATUS_BLOCKING
                    else ACTION_QUEUE_STATUS_OPEN
                ),
                subsystem="readiness",
                operator_action_required=True,
                command_refs=[
                    RUNBOOK_COMMAND_READINESS_SUMMARY,
                    RUNBOOK_COMMAND_DECISION_PACK_SUMMARY,
                ],
                evidence_refs=_readiness_pack_evidence_refs(pack.readiness_summary),
                advisory_notes=_readiness_pack_guidance(pack.readiness_summary),
            )
        ]

    return []


def build_operator_runbook(
    *,
    decision_pack: OperatorDecisionPack,
) -> OperatorRunbookSummary:
    """Build a read-only operator runbook derived from the decision pack.

    Pure computation — no I/O, no DB, no LLM, no side-effects.
    The runbook is advisory only; execution_enabled and write_back_allowed
    are always False.
    Sprint 30.
    """
    steps = _runbook_steps_from_action_queue(decision_pack.action_queue_summary)
    if not steps:
        steps = _runbook_fallback_steps(decision_pack)

    next_steps = steps[:3] if len(steps) > 1 else list(steps)

    command_refs = _unique_strings(
        [ref for step in steps for ref in step.command_refs]
    )
    evidence_refs = _unique_strings(
        list(decision_pack.evidence_refs)
        + [ref for step in steps for ref in step.evidence_refs]
    )
    affected_subsystems = _unique_strings(
        list(decision_pack.affected_subsystems)
        + [step.subsystem for step in steps]
    )
    operator_guidance = _unique_strings(list(decision_pack.operator_guidance))

    return OperatorRunbookSummary(
        overall_status=decision_pack.overall_status,
        blocking_count=decision_pack.blocking_count,
        review_required_count=decision_pack.review_required_count,
        action_queue_count=decision_pack.action_queue_count,
        affected_subsystems=affected_subsystems,
        operator_guidance=operator_guidance,
        evidence_refs=evidence_refs,
        command_refs=command_refs,
        steps=steps,
        next_steps=next_steps,
        generated_at=decision_pack.generated_at,
    )


def save_operator_runbook(
    runbook: OperatorRunbookSummary,
    output_path: Path | str,
) -> Path:
    """Persist the operator runbook summary as structured JSON."""
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(runbook.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved


# ---------------------------------------------------------------------------
# Sprint 33 - Operator Review Journal
# ---------------------------------------------------------------------------


def _review_id(
    *,
    source_ref: str,
    operator_id: str,
    review_action: str,
    created_at: str,
    review_note: str,
) -> str:
    digest = hashlib.sha1(
        "|".join(
            [
                source_ref,
                operator_id,
                review_action,
                created_at,
                review_note,
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"rvw_{digest[:12]}"


def _require_non_blank_string(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must be a non-empty string")
    return normalized


def _require_string_list(values: list[str] | None, *, label: str) -> list[str]:
    if values is None:
        return []
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{label} entries must be strings")
        normalized = value.strip()
        if normalized:
            result.append(normalized)
    return _unique_strings(result)


def _normalize_review_action(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in VALID_REVIEW_ACTIONS:
        allowed = ", ".join(sorted(VALID_REVIEW_ACTIONS))
        raise ValueError(f"review_action must be one of: {allowed}")
    return normalized


def _journal_status_for_action(review_action: str) -> str:
    if review_action == REVIEW_ACTION_RESOLVE:
        return JOURNAL_STATUS_RESOLVED
    return JOURNAL_STATUS_OPEN


def _review_entry_timestamp(entry: ReviewJournalEntry) -> datetime:
    parsed = _parse_iso_timestamp(entry.created_at)
    if parsed is not None:
        return parsed
    return datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class ReviewJournalEntry:
    """Single append-only operator review journal entry."""

    review_id: str
    source_ref: str
    operator_id: str
    review_action: str
    review_note: str
    evidence_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    journal_status: str = JOURNAL_STATUS_OPEN

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "review_journal_entry",
            "review_id": self.review_id,
            "source_ref": self.source_ref,
            "operator_id": self.operator_id,
            "review_action": self.review_action,
            "review_note": self.review_note,
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
            "journal_status": self.journal_status,
        }


@dataclass(frozen=True)
class ReviewJournalSummary:
    """Read-only summary of append-only operator review journal entries."""

    generated_at: str
    journal_path: str
    journal_status: str
    total_count: int
    source_ref_count: int
    open_count: int
    resolved_count: int
    latest_created_at: str | None = None
    entries: list[ReviewJournalEntry] = field(default_factory=list)
    latest_entries: list[ReviewJournalEntry] = field(default_factory=list)
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "review_journal_summary",
            "generated_at": self.generated_at,
            "journal_path": self.journal_path,
            "journal_status": self.journal_status,
            "total_count": self.total_count,
            "source_ref_count": self.source_ref_count,
            "open_count": self.open_count,
            "resolved_count": self.resolved_count,
            "latest_created_at": self.latest_created_at,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "entries": [entry.to_json_dict() for entry in self.entries],
            "latest_entries": [entry.to_json_dict() for entry in self.latest_entries],
        }


@dataclass(frozen=True)
class ReviewResolutionSummary:
    """Read-only latest-resolution projection derived from the review journal."""

    generated_at: str
    journal_path: str
    journal_status: str
    total_count: int
    source_ref_count: int
    open_count: int
    resolved_count: int
    latest_created_at: str | None = None
    open_source_refs: list[str] = field(default_factory=list)
    resolved_source_refs: list[str] = field(default_factory=list)
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "review_resolution_summary",
            "generated_at": self.generated_at,
            "journal_path": self.journal_path,
            "journal_status": self.journal_status,
            "total_count": self.total_count,
            "source_ref_count": self.source_ref_count,
            "open_count": self.open_count,
            "resolved_count": self.resolved_count,
            "latest_created_at": self.latest_created_at,
            "open_source_refs": list(self.open_source_refs),
            "resolved_source_refs": list(self.resolved_source_refs),
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


def create_review_journal_entry(
    *,
    source_ref: str,
    operator_id: str,
    review_action: str,
    review_note: str,
    evidence_refs: list[str] | None = None,
    created_at: str | None = None,
) -> ReviewJournalEntry:
    """Create a validated append-only review journal entry."""
    normalized_created_at = created_at or datetime.now(UTC).isoformat()
    if _parse_iso_timestamp(normalized_created_at) is None:
        raise ValueError("created_at must be a valid ISO-8601 timestamp")

    normalized_source_ref = _require_non_blank_string(source_ref, label="source_ref")
    normalized_operator_id = _require_non_blank_string(operator_id, label="operator_id")
    normalized_review_action = _normalize_review_action(review_action)
    normalized_review_note = _require_non_blank_string(review_note, label="review_note")
    normalized_evidence_refs = _require_string_list(
        evidence_refs,
        label="evidence_refs",
    )

    return ReviewJournalEntry(
        review_id=_review_id(
            source_ref=normalized_source_ref,
            operator_id=normalized_operator_id,
            review_action=normalized_review_action,
            created_at=normalized_created_at,
            review_note=normalized_review_note,
        ),
        source_ref=normalized_source_ref,
        operator_id=normalized_operator_id,
        review_action=normalized_review_action,
        review_note=normalized_review_note,
        evidence_refs=normalized_evidence_refs,
        created_at=normalized_created_at,
        journal_status=_journal_status_for_action(normalized_review_action),
    )


def review_journal_entry_from_dict(payload: dict[str, object]) -> ReviewJournalEntry:
    """Load and validate a ReviewJournalEntry from JSON-compatible data."""
    review_id = _require_non_blank_string(
        str(payload.get("review_id", "")),
        label="review_id",
    )
    entry = create_review_journal_entry(
        source_ref=str(payload.get("source_ref", "")),
        operator_id=str(payload.get("operator_id", "")),
        review_action=str(payload.get("review_action", "")),
        review_note=str(payload.get("review_note", "")),
        evidence_refs=payload.get("evidence_refs"),  # type: ignore[arg-type]
        created_at=str(payload.get("created_at", "")),
    )
    stored_status = _require_non_blank_string(
        str(payload.get("journal_status", "")),
        label="journal_status",
    )
    if stored_status != entry.journal_status:
        raise ValueError("journal_status must match the derived status for review_action")
    return ReviewJournalEntry(
        review_id=review_id,
        source_ref=entry.source_ref,
        operator_id=entry.operator_id,
        review_action=entry.review_action,
        review_note=entry.review_note,
        evidence_refs=list(entry.evidence_refs),
        created_at=entry.created_at,
        journal_status=entry.journal_status,
    )


def append_review_journal_entry_jsonl(
    entry: ReviewJournalEntry,
    path: Path | str,
) -> Path:
    """Append a ReviewJournalEntry as a JSONL row without mutating prior rows."""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry.to_json_dict()) + "\n")
    return resolved


def load_review_journal_entries(path: Path | str) -> list[ReviewJournalEntry]:
    """Load append-only ReviewJournalEntry rows from JSONL, skipping malformed lines."""
    resolved = Path(path)
    if not resolved.exists():
        return []

    entries: list[ReviewJournalEntry] = []
    for raw in resolved.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                continue
            entries.append(review_journal_entry_from_dict(dict(payload)))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return entries


def _latest_review_entries_by_source(
    entries: list[ReviewJournalEntry],
) -> list[ReviewJournalEntry]:
    latest_by_source: dict[str, ReviewJournalEntry] = {}
    for entry in entries:
        existing = latest_by_source.get(entry.source_ref)
        if existing is None or _review_entry_timestamp(entry) >= _review_entry_timestamp(
            existing
        ):
            latest_by_source[entry.source_ref] = entry
    return sorted(
        latest_by_source.values(),
        key=_review_entry_timestamp,
        reverse=True,
    )


def build_review_journal_summary(
    entries: list[ReviewJournalEntry],
    *,
    journal_path: Path | str = DEFAULT_REVIEW_JOURNAL_PATH,
) -> ReviewJournalSummary:
    """Build a read-only journal summary from append-only review entries."""
    latest_entries = _latest_review_entries_by_source(entries)
    open_count = sum(
        1 for entry in latest_entries if entry.journal_status == JOURNAL_STATUS_OPEN
    )
    resolved_count = sum(
        1 for entry in latest_entries if entry.journal_status == JOURNAL_STATUS_RESOLVED
    )
    if open_count:
        journal_status = JOURNAL_STATUS_OPEN
    elif resolved_count:
        journal_status = JOURNAL_STATUS_RESOLVED
    else:
        journal_status = JOURNAL_STATUS_EMPTY

    latest_created_at = (
        max(entries, key=_review_entry_timestamp).created_at if entries else None
    )

    return ReviewJournalSummary(
        generated_at=datetime.now(UTC).isoformat(),
        journal_path=str(Path(journal_path)),
        journal_status=journal_status,
        total_count=len(entries),
        source_ref_count=len(latest_entries),
        open_count=open_count,
        resolved_count=resolved_count,
        latest_created_at=latest_created_at,
        entries=list(entries),
        latest_entries=latest_entries,
    )


def build_review_resolution_summary(
    summary: ReviewJournalSummary,
) -> ReviewResolutionSummary:
    """Project latest per-source resolution state from the journal summary only."""
    open_source_refs = [
        entry.source_ref
        for entry in summary.latest_entries
        if entry.journal_status == JOURNAL_STATUS_OPEN
    ]
    resolved_source_refs = [
        entry.source_ref
        for entry in summary.latest_entries
        if entry.journal_status == JOURNAL_STATUS_RESOLVED
    ]
    return ReviewResolutionSummary(
        generated_at=summary.generated_at,
        journal_path=summary.journal_path,
        journal_status=summary.journal_status,
        total_count=summary.total_count,
        source_ref_count=summary.source_ref_count,
        open_count=summary.open_count,
        resolved_count=summary.resolved_count,
        latest_created_at=summary.latest_created_at,
        open_source_refs=open_source_refs,
        resolved_source_refs=resolved_source_refs,
    )






