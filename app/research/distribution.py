"""Signal distribution and route profiling for A/B/C inference architecture.

Sprint 14 - validates distribution of signals across primary, shadow, and control tiers.
Sprint 19 - route-aware delivery classification (production_delivery / shadow_audit /
control_audit) and RouteAwareDistributionSummary.
"""

from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.core.domain.document import CanonicalDocument
from app.core.enums import AnalysisSource
from app.research.abc_result import ABCInferenceEnvelope, PathResultEnvelope
from app.research.execution_handoff import (
    HandoffAcknowledgement,
    SignalHandoff,
    classify_delivery_for_route,
    create_signal_handoff,
)
from app.research.signals import SignalCandidate
from app.storage.repositories.document_repo import DocumentRepository

# ---------------------------------------------------------------------------
# Sprint 19 — delivery class constants (I-109)
# ---------------------------------------------------------------------------

_DELIVERY_CLASS_PRODUCTION = "production_delivery"
_DELIVERY_CLASS_SHADOW_AUDIT = "shadow_audit"
_DELIVERY_CLASS_CONTROL_AUDIT = "control_audit"


@dataclass
class TierProfile:
    """Summary of performance for a given tier/source."""
    document_count: int = 0
    signal_count: int = 0
    spam_count: int = 0
    avg_priority: float = 0.0
    actionable_count: int = 0

    def to_json_dict(self) -> dict[str, object]:
        return {
            "document_count": self.document_count,
            "signal_count": self.signal_count,
            "spam_count": self.spam_count,
            "avg_priority": round(self.avg_priority, 4),
            "actionable_count": self.actionable_count,
        }


@dataclass
class RouteProfileReport:
    """End-to-end signal distribution report across all tiers (A/B/C)."""

    total_analyzed: int
    primary_tier_metrics: dict[str, TierProfile]
    shadow_in_metadata: int = 0
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "route_profile",
            "generated_at": self.generated_at,
            "total_analyzed": self.total_analyzed,
            "primary_distribution": {
                tier: profile.to_json_dict() for tier, profile in self.primary_tier_metrics.items()
            },
            "shadow_executions_tracked": self.shadow_in_metadata,
        }


@dataclass
class ExecutionHandoffReport:
    """Controlled external-consumption report for qualified primary signals only."""

    signal_count: int
    signals: list[SignalHandoff]
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False
    notes: list[str] = field(
        default_factory=lambda: [
            "Signals are advisory only.",
            "No trading execution or write-back is exposed by this surface.",
            "Only primary-path signals are consumer-visible; shadow/control remain audit-only.",
        ]
    )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "execution_signal_handoff",
            "generated_at": self.generated_at,
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "signal_count": self.signal_count,
            "signals": [signal.to_json_dict() for signal in self.signals],
            "notes": list(self.notes),
        }


@dataclass
class DistributionAuditRecord:
    """Audit-only route output classification derived from persisted ABC envelopes."""

    document_id: str
    route_profile: str
    active_primary_path: str
    path_id: str
    provider: str
    analysis_source: str
    path_type: str
    delivery_class: str
    consumer_visibility: str
    audit_visibility: str
    comparison_labels: list[str] = field(default_factory=list)
    summary: str | None = None
    result_ref: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "route_profile": self.route_profile,
            "active_primary_path": self.active_primary_path,
            "path_id": self.path_id,
            "provider": self.provider,
            "analysis_source": self.analysis_source,
            "path_type": self.path_type,
            "delivery_class": self.delivery_class,
            "consumer_visibility": self.consumer_visibility,
            "audit_visibility": self.audit_visibility,
            "comparison_labels": list(self.comparison_labels),
            "summary": self.summary,
            "result_ref": self.result_ref,
        }


@dataclass
class DistributionClassificationReport:
    """Route-aware read-only delivery report across productive and audit surfaces."""

    primary_handoff: ExecutionHandoffReport
    audit_outputs: list[DistributionAuditRecord]
    route_profiles: list[str]
    active_primary_paths: list[str]
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False
    notes: list[str] = field(
        default_factory=lambda: [
            "Primary-qualified signals remain the only productive handoff surface.",
            "Shadow/control outputs are separated as audit and comparison records only.",
        ]
    )

    def to_json_dict(self) -> dict[str, object]:
        shadow_count = sum(1 for output in self.audit_outputs if output.path_type == "shadow")
        control_count = sum(
            1 for output in self.audit_outputs if output.path_type == "control"
        )
        return {
            "report_type": "distribution_classification_report",
            "generated_at": self.generated_at,
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "route_profiles": list(self.route_profiles),
            "active_primary_paths": list(self.active_primary_paths),
            "primary_signal_count": self.primary_handoff.signal_count,
            "audit_output_count": len(self.audit_outputs),
            "shadow_output_count": shadow_count,
            "control_output_count": control_count,
            "primary_handoff": self.primary_handoff.to_json_dict(),
            "audit_outputs": [output.to_json_dict() for output in self.audit_outputs],
            "notes": list(self.notes),
        }


@dataclass
class HandoffCollectorEntry:
    """Single collector status row derived from handoff + acknowledgement audit."""

    handoff_id: str
    signal_id: str
    status: str
    path_type: str
    delivery_class: str
    consumer_visibility: str
    audit_visibility: str
    consumer_agent_id: str | None = None
    acknowledged_at: str | None = None
    ack_event_count: int = 0

    def to_json_dict(self) -> dict[str, object]:
        return {
            "handoff_id": self.handoff_id,
            "signal_id": self.signal_id,
            "status": self.status,
            "path_type": self.path_type,
            "delivery_class": self.delivery_class,
            "consumer_visibility": self.consumer_visibility,
            "audit_visibility": self.audit_visibility,
            "consumer_agent_id": self.consumer_agent_id,
            "acknowledged_at": self.acknowledged_at,
            "ack_event_count": self.ack_event_count,
        }


@dataclass
class HandoffCollectorSummaryReport:
    """Collector summary for productive handoffs and their audit-only acknowledgements."""

    total_handoffs: int
    acknowledged_count: int
    pending_count: int
    ack_event_count: int
    consumers: dict[str, int]
    acknowledged_handoffs: list[HandoffCollectorEntry]
    pending_handoffs: list[HandoffCollectorEntry]
    orphaned_ack_count: int = 0
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "handoff_collector_summary",
            "generated_at": self.generated_at,
            "interface_mode": self.interface_mode,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
            "total_handoffs": self.total_handoffs,
            "acknowledged_count": self.acknowledged_count,
            "pending_count": self.pending_count,
            "ack_event_count": self.ack_event_count,
            "orphaned_ack_count": self.orphaned_ack_count,
            "consumers": dict(self.consumers),
            "acknowledged_handoffs": [
                entry.to_json_dict() for entry in self.acknowledged_handoffs
            ],
            "pending_handoffs": [entry.to_json_dict() for entry in self.pending_handoffs],
        }


async def build_route_profile(repo: DocumentRepository, limit: int = 1000) -> RouteProfileReport:
    """Build a distribution summary mapping out the A/B/C pathways."""
    documents = await repo.get_recent_analyzed(limit=limit)

    tier_metrics: collections.defaultdict[str, TierProfile] = collections.defaultdict(
        TierProfile
    )
    shadow_count = 0

    for doc in documents:
        if doc.metadata.get("shadow_analysis"):
            shadow_count += 1

        source = (
            doc.analysis_source.value
            if doc.analysis_source
            else AnalysisSource.EXTERNAL_LLM.value
        )
        profile = tier_metrics[source]

        profile.document_count += 1
        if doc.priority_score and doc.priority_score >= 8:
            profile.signal_count += 1
        if doc.spam_probability and doc.spam_probability > 0.8:
            profile.spam_count += 1
        if doc.metadata.get("actionable", False):
            profile.actionable_count += 1

        current_sum = profile.avg_priority * (profile.document_count - 1)
        profile.avg_priority = (current_sum + (doc.priority_score or 0)) / profile.document_count

    return RouteProfileReport(
        total_analyzed=len(documents),
        primary_tier_metrics=dict(tier_metrics),
        shadow_in_metadata=shadow_count,
    )


def build_execution_handoff_report(
    signals: list[SignalCandidate],
    documents: list[CanonicalDocument],
) -> ExecutionHandoffReport:
    """Compose a read-only external signal handoff from existing SignalCandidates."""
    documents_by_id = {str(document.id): document for document in documents}
    handoff_signals: list[SignalHandoff] = []

    for signal in signals:
        document = documents_by_id.get(signal.document_id)
        if document is None:
            raise ValueError(
                "Execution handoff requires the source document for every signal: "
                f"{signal.document_id}"
            )

        handoff_signals.append(create_signal_handoff(signal, document=document))

    return ExecutionHandoffReport(
        signal_count=len(handoff_signals),
        signals=handoff_signals,
    )


def _comparison_labels_for_path(
    envelope: ABCInferenceEnvelope,
    path: PathResultEnvelope,
) -> list[str]:
    path_prefix = path.path_id.split(".", 1)[0].upper()
    compared_suffix = f"_{path_prefix}"
    labels = [
        summary.compared_path
        for summary in envelope.comparison_summary
        if summary.compared_path.endswith(compared_suffix)
    ]
    if labels:
        return labels
    if path_prefix in {"B", "C"}:
        return [f"A_vs_{path_prefix}"]
    return []


def build_distribution_classification_report(
    signals: list[SignalCandidate],
    documents: list[CanonicalDocument],
    envelopes: list[ABCInferenceEnvelope],
) -> DistributionClassificationReport:
    """Compose productive primary handoffs plus audit-only shadow/control outputs."""
    handoff_report = build_execution_handoff_report(signals, documents)
    audit_outputs: list[DistributionAuditRecord] = []

    for envelope in envelopes:
        active_primary_path = (
            envelope.distribution_metadata.active_primary_path
            if envelope.distribution_metadata is not None
            else envelope.primary_result.path_id
        )

        comparison_paths = list(envelope.shadow_results)
        if envelope.control_result is not None:
            comparison_paths.append(envelope.control_result)

        for path in comparison_paths:
            classification = classify_delivery_for_route(path.path_id)
            audit_outputs.append(
                DistributionAuditRecord(
                    document_id=envelope.document_id,
                    route_profile=envelope.route_profile,
                    active_primary_path=active_primary_path,
                    path_id=path.path_id,
                    provider=path.provider,
                    analysis_source=path.analysis_source,
                    path_type=classification.path_type,
                    delivery_class=classification.delivery_class,
                    consumer_visibility=classification.consumer_visibility,
                    audit_visibility=classification.audit_visibility,
                    comparison_labels=_comparison_labels_for_path(envelope, path),
                    summary=path.summary,
                    result_ref=path.result_ref,
                )
            )

    route_profiles = sorted({envelope.route_profile for envelope in envelopes})
    active_primary_paths = sorted(
        {
            envelope.distribution_metadata.active_primary_path
            if envelope.distribution_metadata is not None
            else envelope.primary_result.path_id
            for envelope in envelopes
        }
    )

    return DistributionClassificationReport(
        primary_handoff=handoff_report,
        audit_outputs=audit_outputs,
        route_profiles=route_profiles,
        active_primary_paths=active_primary_paths,
    )

# ---------------------------------------------------------------------------
# Sprint 19 — route-aware delivery classification
# ---------------------------------------------------------------------------


def classify_delivery_class(route_path: str) -> str:
    """Map a route_path letter prefix to its delivery class (I-109, I-110).

    A.* → production_delivery   (primary — leads all consumption decisions)
    B.* → shadow_audit          (shadow — audit only, never production-routed)
    C.* → control_audit         (control — audit only, never production-routed)
    Any unknown prefix → production_delivery (safe default, I-110)
    """
    letter = route_path.split(".")[0].upper() if route_path else "A"
    if letter == "B":
        return _DELIVERY_CLASS_SHADOW_AUDIT
    if letter == "C":
        return _DELIVERY_CLASS_CONTROL_AUDIT
    return _DELIVERY_CLASS_PRODUCTION


@dataclass
class RouteAwareDistributionSummary:
    """Aggregated delivery-class counts across a set of SignalHandoff artifacts (I-111).

    Primary signals (A.*) lead all consumption decisions.
    Shadow (B.*) and control (C.*) signals are audit-only — never mixed with
    production delivery (I-112, I-113).
    """

    production_count: int = 0
    shadow_audit_count: int = 0
    control_audit_count: int = 0
    total_count: int = 0
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "route_aware_distribution_summary",
            "generated_at": self.generated_at,
            "total_count": self.total_count,
            "production_count": self.production_count,
            "shadow_audit_count": self.shadow_audit_count,
            "control_audit_count": self.control_audit_count,
            "delivery_class_production": _DELIVERY_CLASS_PRODUCTION,
            "delivery_class_shadow": _DELIVERY_CLASS_SHADOW_AUDIT,
            "delivery_class_control": _DELIVERY_CLASS_CONTROL_AUDIT,
        }


def build_route_aware_distribution_summary(
    handoffs: list[SignalHandoff],
) -> RouteAwareDistributionSummary:
    """Build a delivery-class breakdown from a list of SignalHandoff artifacts (I-114).

    Shadow and control counts are tracked for audit visibility but must never
    be mixed into the production delivery set (I-113).
    """
    production = 0
    shadow = 0
    control = 0

    for handoff in handoffs:
        dc = classify_delivery_class(handoff.route_path)
        if dc == _DELIVERY_CLASS_SHADOW_AUDIT:
            shadow += 1
        elif dc == _DELIVERY_CLASS_CONTROL_AUDIT:
            control += 1
        else:
            production += 1

    return RouteAwareDistributionSummary(
        production_count=production,
        shadow_audit_count=shadow,
        control_audit_count=control,
        total_count=len(handoffs),
    )


def save_route_profile(report: RouteProfileReport, output_path: Path | str) -> Path:
    """Persist distribution stats to JSON."""
    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(report.to_json_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return resolved_path


def save_execution_handoff_report(
    report: ExecutionHandoffReport,
    output_path: Path | str,
) -> Path:
    """Persist a read-only execution handoff report as structured JSON."""
    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(report.to_json_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return resolved_path


def save_distribution_classification_report(
    report: DistributionClassificationReport,
    output_path: Path | str,
) -> Path:
    """Persist a route-aware distribution classification report as structured JSON."""
    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(report.to_json_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return resolved_path


def build_handoff_collector_summary(
    handoffs: list[SignalHandoff],
    acknowledgements: list[HandoffAcknowledgement],
) -> HandoffCollectorSummaryReport:
    """Build pending/acknowledged collector state from existing audit artifacts only."""
    latest_ack_by_handoff: dict[str, HandoffAcknowledgement] = {}
    ack_counts_by_handoff: collections.defaultdict[str, int] = collections.defaultdict(int)
    consumer_counts: collections.defaultdict[str, int] = collections.defaultdict(int)

    known_handoff_ids = {handoff.handoff_id for handoff in handoffs}
    orphaned_ack_count = 0

    for acknowledgement in acknowledgements:
        if acknowledgement.handoff_id not in known_handoff_ids:
            orphaned_ack_count += 1
            continue
        latest_ack_by_handoff[acknowledgement.handoff_id] = acknowledgement
        ack_counts_by_handoff[acknowledgement.handoff_id] += 1
        consumer_counts[acknowledgement.consumer_agent_id] += 1

    acknowledged_handoffs: list[HandoffCollectorEntry] = []
    pending_handoffs: list[HandoffCollectorEntry] = []

    for handoff in handoffs:
        latest_ack: HandoffAcknowledgement | None = latest_ack_by_handoff.get(handoff.handoff_id)
        entry = HandoffCollectorEntry(
            handoff_id=handoff.handoff_id,
            signal_id=handoff.signal_id,
            status="acknowledged" if latest_ack is not None else "pending",
            path_type=handoff.path_type,
            delivery_class=handoff.delivery_class,
            consumer_visibility=handoff.consumer_visibility,
            audit_visibility=handoff.audit_visibility,
            consumer_agent_id=(
                latest_ack.consumer_agent_id if latest_ack is not None else None
            ),
            acknowledged_at=(
                latest_ack.acknowledged_at if latest_ack is not None else None
            ),
            ack_event_count=ack_counts_by_handoff.get(handoff.handoff_id, 0),
        )
        if latest_ack is not None:
            acknowledged_handoffs.append(entry)
        else:
            pending_handoffs.append(entry)

    return HandoffCollectorSummaryReport(
        total_handoffs=len(handoffs),
        acknowledged_count=len(acknowledged_handoffs),
        pending_count=len(pending_handoffs),
        ack_event_count=sum(ack_counts_by_handoff.values()),
        consumers=dict(consumer_counts),
        acknowledged_handoffs=acknowledged_handoffs,
        pending_handoffs=pending_handoffs,
        orphaned_ack_count=orphaned_ack_count,
    )


def save_handoff_collector_summary(
    report: HandoffCollectorSummaryReport,
    output_path: Path | str,
) -> Path:
    """Persist a handoff collector summary as structured JSON."""
    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(report.to_json_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return resolved_path
