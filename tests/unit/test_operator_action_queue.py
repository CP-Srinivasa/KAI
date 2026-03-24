"""Focused tests for the canonical Sprint-28 operator action queue surface."""

from __future__ import annotations

from dataclasses import replace

from app.research.artifact_lifecycle import (
    ARTIFACT_CLASS_UNKNOWN,
    ARTIFACT_STATUS_CURRENT,
    RETENTION_CLASS_REVIEW_REQUIRED,
    ArtifactRetentionEntry,
    ReviewRequiredArtifactSummary,
)
from app.research.distribution import build_handoff_collector_summary
from app.research.operational_readiness import (
    ACTION_PRIORITY_P1,
    ACTION_PRIORITY_P2,
    ACTION_QUEUE_STATUS_BLOCKING,
    ACTION_QUEUE_STATUS_CLEAR,
    ACTION_QUEUE_STATUS_OPEN,
    ACTION_QUEUE_STATUS_REVIEW_REQUIRED,
    GATE_STATUS_ADVISORY,
    GATE_STATUS_BLOCKING,
    GATE_STATUS_WARNING,
    OperationalArtifactRefs,
    ProtectiveGateItem,
    ProtectiveGateSummary,
    build_action_queue_summary,
    build_blocking_actions,
    build_operational_escalation_summary,
    build_operational_readiness_report,
    build_prioritized_actions,
    build_review_required_actions,
)


def _make_empty_readiness_report():
    collector_summary = build_handoff_collector_summary([], [])
    return build_operational_readiness_report(
        handoffs=[],
        collector_summary=collector_summary,
        artifacts=OperationalArtifactRefs(),
    )


def _make_review_required_summary() -> ReviewRequiredArtifactSummary:
    entry = ArtifactRetentionEntry(
        name="manual_review_blob.json",
        path="manual_review_blob.json",
        size_bytes=2,
        modified_at="2026-03-20T00:00:00+00:00",
        age_days=1.0,
        status=ARTIFACT_STATUS_CURRENT,
        artifact_class=ARTIFACT_CLASS_UNKNOWN,
        retention_class=RETENTION_CLASS_REVIEW_REQUIRED,
        protected=False,
        rotatable=False,
        retention_rationale="Unknown artifacts fail closed until an operator classifies them.",
        operator_guidance="Confirm the artifact type before any archival decision.",
    )
    return ReviewRequiredArtifactSummary(
        generated_at="2026-03-20T00:00:00+00:00",
        artifacts_dir="artifacts",
        review_required_count=1,
        entries=(entry,),
    )


def test_build_action_queue_summary_projects_and_prioritizes_actions() -> None:
    readiness_report = replace(
        _make_empty_readiness_report(),
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_BLOCKING,
            blocking_count=1,
            warning_count=1,
            advisory_count=0,
            items=[
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_WARNING,
                    severity="warning",
                    category="provider_health",
                    summary="Primary provider drift requires operator attention.",
                    subsystem="providers",
                    recommended_actions=["Check the provider before further rollout."],
                    evidence_refs=["artifacts/provider_health.json"],
                ),
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_BLOCKING,
                    severity="critical",
                    category="artifact_state",
                    summary="ABC output is missing for the active route.",
                    subsystem="routing",
                    blocking_reason="Shadow/control audit output is unavailable.",
                    recommended_actions=["Restore the ABC audit artifact first."],
                    evidence_refs=["artifacts/abc_output.jsonl"],
                ),
            ],
        ),
    )

    escalation = build_operational_escalation_summary(
        readiness_report,
        review_required_summary=_make_review_required_summary(),
    )
    summary = build_action_queue_summary(escalation)

    assert summary.queue_status == ACTION_QUEUE_STATUS_BLOCKING
    assert summary.total_count == 3
    assert summary.blocking_count == 1
    assert summary.open_count == 1
    assert summary.review_required_count == 1
    assert summary.highest_priority == ACTION_PRIORITY_P1
    assert summary.execution_enabled is False
    assert summary.write_back_allowed is False
    assert summary.items[0].queue_status == ACTION_QUEUE_STATUS_BLOCKING
    assert summary.items[0].priority == ACTION_PRIORITY_P1
    assert summary.items[0].action_id.startswith("act_")
    assert summary.items[1].queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    assert summary.items[1].priority == ACTION_PRIORITY_P2
    assert summary.items[2].queue_status == ACTION_QUEUE_STATUS_OPEN
    assert summary.items[2].priority == ACTION_PRIORITY_P2
    assert "artifacts/abc_output.jsonl" in summary.evidence_refs


def test_build_blocking_actions_filters_only_blocking_rows() -> None:
    readiness_report = replace(
        _make_empty_readiness_report(),
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_BLOCKING,
            blocking_count=1,
            warning_count=1,
            advisory_count=0,
            items=[
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_BLOCKING,
                    severity="critical",
                    category="artifact_state",
                    summary="ABC output is missing for the active route.",
                    subsystem="routing",
                    blocking_reason="Shadow/control audit output is unavailable.",
                    recommended_actions=["Restore the ABC audit artifact first."],
                    evidence_refs=["artifacts/abc_output.jsonl"],
                ),
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_WARNING,
                    severity="warning",
                    category="provider_health",
                    summary="Primary provider drift requires operator attention.",
                    subsystem="providers",
                    recommended_actions=["Check the provider before further rollout."],
                    evidence_refs=["artifacts/provider_health.json"],
                ),
            ],
        ),
    )

    queue = build_action_queue_summary(build_operational_escalation_summary(readiness_report))
    summary = build_blocking_actions(queue)

    assert summary.queue_status == ACTION_QUEUE_STATUS_BLOCKING
    assert summary.blocking_count == 1
    assert summary.highest_priority == ACTION_PRIORITY_P1
    assert len(summary.items) == 1
    assert all(item.queue_status == ACTION_QUEUE_STATUS_BLOCKING for item in summary.items)
    assert summary.execution_enabled is False
    assert summary.write_back_allowed is False


def test_build_prioritized_actions_preserves_priority_order() -> None:
    readiness_report = replace(
        _make_empty_readiness_report(),
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_WARNING,
            blocking_count=0,
            warning_count=2,
            advisory_count=0,
            items=[
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_WARNING,
                    severity="warning",
                    category="handoff_backlog",
                    summary="Pending handoffs exceeded the guarded threshold.",
                    subsystem="handoff",
                    recommended_actions=["Review the pending handoffs first."],
                    evidence_refs=["artifacts/handoffs.jsonl"],
                ),
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_WARNING,
                    severity="warning",
                    category="provider_health",
                    summary="Primary provider drift requires operator attention.",
                    subsystem="providers",
                    recommended_actions=["Check the provider before further rollout."],
                    evidence_refs=["artifacts/provider_health.json"],
                ),
            ],
        ),
    )

    queue = build_action_queue_summary(
        build_operational_escalation_summary(
            readiness_report,
            review_required_summary=_make_review_required_summary(),
        )
    )
    summary = build_prioritized_actions(queue)

    assert summary.queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    assert summary.action_count == 3
    assert summary.highest_priority == ACTION_PRIORITY_P2
    assert [item.priority for item in summary.items] == [
        ACTION_PRIORITY_P2,
        ACTION_PRIORITY_P2,
        ACTION_PRIORITY_P2,
    ]
    assert summary.execution_enabled is False
    assert summary.write_back_allowed is False


def test_build_review_required_actions_filters_only_review_required_rows() -> None:
    queue = build_action_queue_summary(
        build_operational_escalation_summary(
            _make_empty_readiness_report(),
            review_required_summary=_make_review_required_summary(),
        )
    )
    summary = build_review_required_actions(queue)

    assert summary.queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    assert summary.review_required_count == 1
    assert summary.highest_priority == ACTION_PRIORITY_P2
    assert len(summary.items) == 1
    assert summary.items[0].queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    assert summary.items[0].blocking is False
    assert summary.items[0].operator_action_required is True
    assert summary.execution_enabled is False
    assert summary.write_back_allowed is False


def test_action_queue_ignores_advisory_only_rows() -> None:
    readiness_report = replace(
        _make_empty_readiness_report(),
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_ADVISORY,
            blocking_count=0,
            warning_count=0,
            advisory_count=1,
            items=[
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_ADVISORY,
                    severity="info",
                    category="distribution_drift",
                    summary="A minor audit visibility mismatch was observed.",
                    subsystem="distribution",
                    recommended_actions=[
                        "Observe the audit-only variance; no write-back is permitted."
                    ],
                    evidence_refs=["artifacts/distribution_report.json"],
                )
            ],
        ),
    )

    summary = build_action_queue_summary(build_operational_escalation_summary(readiness_report))

    assert summary.queue_status == ACTION_QUEUE_STATUS_CLEAR
    assert summary.total_count == 0
    assert summary.open_count == 0
    assert summary.blocking_count == 0
    assert summary.review_required_count == 0
    assert summary.highest_priority == "none"
    assert summary.items == []
    assert summary.execution_enabled is False
    assert summary.write_back_allowed is False
