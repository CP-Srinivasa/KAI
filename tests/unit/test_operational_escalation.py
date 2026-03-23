"""Focused tests for the canonical Sprint-27 escalation surface."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
    CATEGORY_REVIEW_REQUIRED,
    GATE_STATUS_ADVISORY,
    GATE_STATUS_BLOCKING,
    GATE_STATUS_CLEAR,
    GATE_STATUS_WARNING,
    SEVERITY_CRITICAL,
    OperationalArtifactRefs,
    ProtectiveGateItem,
    ProtectiveGateSummary,
    build_action_queue_summary,
    build_blocking_actions,
    build_blocking_summary,
    build_daily_operator_summary,
    build_operational_escalation_summary,
    build_operational_readiness_report,
    build_operator_action_summary,
    build_operator_decision_pack,
    build_prioritized_actions,
    build_review_required_actions,
    save_operational_escalation_summary,
    save_operator_decision_pack,
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


def test_build_operational_escalation_summary_is_nominal_without_inputs() -> None:
    summary = build_operational_escalation_summary(_make_empty_readiness_report())

    assert summary.escalation_status == GATE_STATUS_CLEAR
    assert summary.severity == "none"
    assert summary.blocking is False
    assert summary.blocking_count == 0
    assert summary.warning_count == 0
    assert summary.review_required_count == 0
    assert summary.operator_action_count == 0
    assert summary.items == []
    assert summary.execution_enabled is False
    assert summary.write_back_allowed is False


def test_build_operational_escalation_summary_projects_gate_and_review_items() -> None:
    readiness_report = _make_empty_readiness_report()
    gate_item = ProtectiveGateItem(
        gate_status=GATE_STATUS_BLOCKING,
        severity=SEVERITY_CRITICAL,
        category="handoff_backlog",
        summary="Pending handoffs exceeded the guarded threshold.",
        subsystem="handoff",
        blocking_reason="Consumer backlog is above the configured limit.",
        recommended_actions=["Review pending handoffs before further distribution."],
        evidence_refs=["artifacts/handoffs.jsonl"],
    )
    readiness_report = replace(
        readiness_report,
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_BLOCKING,
            blocking_count=1,
            warning_count=0,
            advisory_count=0,
            items=[gate_item],
        ),
    )

    escalation = build_operational_escalation_summary(
        readiness_report,
        review_required_summary=_make_review_required_summary(),
    )

    assert escalation.escalation_status == GATE_STATUS_BLOCKING
    assert escalation.severity == SEVERITY_CRITICAL
    assert escalation.blocking is True
    assert escalation.blocking_count == 1
    assert escalation.review_required_count == 1
    assert escalation.operator_action_count == 2
    assert len(escalation.items) == 2
    assert any(item.blocking for item in escalation.items)
    assert any(item.category == CATEGORY_REVIEW_REQUIRED for item in escalation.items)
    assert "artifacts/handoffs.jsonl" in escalation.evidence_refs
    assert "manual_review_blob.json" in " ".join(escalation.evidence_refs)


def test_build_blocking_summary_filters_only_blocking_rows() -> None:
    readiness_report = _make_empty_readiness_report()
    blocking_gate = ProtectiveGateItem(
        gate_status=GATE_STATUS_BLOCKING,
        severity=SEVERITY_CRITICAL,
        category="artifact_state",
        summary="ABC output is missing for the active route.",
        subsystem="routing",
        blocking_reason="Shadow/control audit output is unavailable.",
        recommended_actions=["Restore the ABC audit artifact before comparing routes."],
        evidence_refs=["artifacts/abc_output.jsonl"],
    )
    warning_gate = ProtectiveGateItem(
        gate_status=GATE_STATUS_WARNING,
        severity="warning",
        category="provider_health",
        summary="Companion provider shows intermittent failures.",
        subsystem="providers",
        recommended_actions=["Inspect the companion runtime before promotion decisions."],
        evidence_refs=["artifacts/readiness_report.json"],
    )
    readiness_report = replace(
        readiness_report,
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_BLOCKING,
            blocking_count=1,
            warning_count=1,
            advisory_count=0,
            items=[blocking_gate, warning_gate],
        ),
    )

    summary = build_blocking_summary(build_operational_escalation_summary(readiness_report))

    assert summary.blocking is True
    assert summary.blocking_count == 1
    assert len(summary.items) == 1
    assert summary.items[0].blocking is True
    assert summary.items[0].category == "artifact_state"
    assert summary.execution_enabled is False
    assert summary.write_back_allowed is False


def test_build_operator_action_summary_includes_review_required_rows() -> None:
    readiness_report = _make_empty_readiness_report()
    warning_gate = ProtectiveGateItem(
        gate_status=GATE_STATUS_WARNING,
        severity="warning",
        category="provider_health",
        summary="Primary provider drift requires operator attention.",
        subsystem="providers",
        recommended_actions=["Confirm the primary provider state before further rollout."],
        evidence_refs=["artifacts/provider_health.json"],
    )
    readiness_report = replace(
        readiness_report,
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_WARNING,
            blocking_count=0,
            warning_count=1,
            advisory_count=0,
            items=[warning_gate],
        ),
    )

    escalation = build_operational_escalation_summary(
        readiness_report,
        review_required_summary=_make_review_required_summary(),
    )
    summary = build_operator_action_summary(escalation)

    assert summary.blocking is False
    assert summary.operator_action_count == 2
    assert summary.review_required_count == 1
    assert len(summary.items) == 2
    assert any(item.category == CATEGORY_REVIEW_REQUIRED for item in summary.items)
    assert summary.execution_enabled is False
    assert summary.write_back_allowed is False


def test_review_required_rows_keep_evidence_and_operator_guidance_visible() -> None:
    summary = build_operational_escalation_summary(
        _make_empty_readiness_report(),
        review_required_summary=_make_review_required_summary(),
    )

    assert summary.escalation_status == GATE_STATUS_WARNING
    assert summary.blocking is False
    assert summary.review_required_count == 1
    assert summary.operator_action_count == 1
    assert len(summary.items) == 1

    item = summary.items[0]
    assert item.category == CATEGORY_REVIEW_REQUIRED
    assert item.blocking is False
    assert item.blocking_reason is None
    assert item.operator_action_required is True
    assert item.evidence_refs == ["manual_review_blob.json", ARTIFACT_CLASS_UNKNOWN]
    assert item.advisory_notes == [
        "Unknown artifacts fail closed until an operator classifies them.",
        "Confirm the artifact type before any archival decision.",
    ]
    assert summary.evidence_refs == item.evidence_refs
    assert summary.advisory_notes == item.advisory_notes
    assert build_blocking_summary(summary).items == []


def test_advisory_rows_stay_read_only_and_do_not_require_operator_action() -> None:
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

    escalation = build_operational_escalation_summary(readiness_report)
    blocking_summary = build_blocking_summary(escalation)
    operator_action_summary = build_operator_action_summary(escalation)

    assert escalation.escalation_status == GATE_STATUS_ADVISORY
    assert escalation.blocking is False
    assert escalation.blocking_count == 0
    assert escalation.advisory_count == 1
    assert escalation.operator_action_count == 0
    assert escalation.items[0].operator_action_required is False
    assert escalation.execution_enabled is False
    assert escalation.write_back_allowed is False

    assert blocking_summary.escalation_status == GATE_STATUS_CLEAR
    assert blocking_summary.blocking is False
    assert blocking_summary.blocking_count == 0
    assert blocking_summary.items == []
    assert blocking_summary.execution_enabled is False
    assert blocking_summary.write_back_allowed is False

    assert operator_action_summary.escalation_status == GATE_STATUS_CLEAR
    assert operator_action_summary.blocking is False
    assert operator_action_summary.operator_action_count == 0
    assert operator_action_summary.review_required_count == 0
    assert operator_action_summary.items == []
    assert operator_action_summary.execution_enabled is False
    assert operator_action_summary.write_back_allowed is False


def test_save_operational_escalation_summary_writes_valid_json(tmp_path: Path) -> None:
    summary = build_operational_escalation_summary(_make_empty_readiness_report())
    out_path = tmp_path / "reports" / "escalation_summary.json"

    result = save_operational_escalation_summary(summary, out_path)

    assert result == out_path
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "operational_escalation_summary"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert payload["items"] == []


@pytest.mark.asyncio
async def test_get_mcp_capabilities_lists_canonical_escalation_tools() -> None:
    from app.agents.mcp_server import get_mcp_capabilities

    payload = json.loads(await get_mcp_capabilities())
    assert "get_escalation_summary" in payload["read_tools"]
    assert "get_blocking_summary" in payload["read_tools"]
    assert "get_operator_action_summary" in payload["read_tools"]




def _make_blocking_escalation():
    """Escalation summary with one blocking + one review-required item."""
    readiness_report = replace(
        _make_empty_readiness_report(),
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_BLOCKING,
            blocking_count=1,
            warning_count=0,
            advisory_count=0,
            items=[
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_BLOCKING,
                    severity=SEVERITY_CRITICAL,
                    category="handoff_backlog",
                    summary="Pending handoffs exceeded the guarded threshold.",
                    subsystem="handoff",
                    blocking_reason="Consumer backlog is above the configured limit.",
                    recommended_actions=["Review pending handoffs before further distribution."],
                    evidence_refs=["artifacts/handoffs.jsonl"],
                )
            ],
        ),
    )
    return build_operational_escalation_summary(
        readiness_report,
        review_required_summary=_make_review_required_summary(),
    )


def test_build_action_queue_summary_is_clear_without_operator_action_items() -> None:
    escalation = build_operational_escalation_summary(_make_empty_readiness_report())
    queue = build_action_queue_summary(escalation)

    assert queue.queue_status == ACTION_QUEUE_STATUS_CLEAR
    assert queue.total_count == 0
    assert queue.blocking_count == 0
    assert queue.review_required_count == 0
    assert queue.items == []
    assert queue.execution_enabled is False
    assert queue.write_back_allowed is False
    assert queue.interface_mode == "read_only"


def test_build_action_queue_summary_projects_blocking_and_review_required_items() -> None:
    escalation = _make_blocking_escalation()
    queue = build_action_queue_summary(escalation)

    assert queue.queue_status == ACTION_QUEUE_STATUS_BLOCKING
    assert queue.total_count == 2
    assert queue.blocking_count == 1
    assert queue.review_required_count == 1
    assert queue.execution_enabled is False
    assert queue.write_back_allowed is False

    priorities = [item.priority for item in queue.items]
    assert ACTION_PRIORITY_P1 in priorities  # blocking item → p1


def test_build_action_queue_summary_action_id_is_deterministic() -> None:
    escalation = _make_blocking_escalation()
    q1 = build_action_queue_summary(escalation)
    q2 = build_action_queue_summary(escalation)

    ids1 = [item.action_id for item in q1.items]
    ids2 = [item.action_id for item in q2.items]
    assert ids1 == ids2
    for action_id in ids1:
        assert action_id.startswith("act_")
        assert len(action_id) == 16  # "act_" + 12 hex chars


def test_build_action_queue_summary_blocking_item_has_p1_priority() -> None:
    escalation = _make_blocking_escalation()
    queue = build_action_queue_summary(escalation)

    blocking_items = [
        item for item in queue.items if item.queue_status == ACTION_QUEUE_STATUS_BLOCKING
    ]
    assert len(blocking_items) == 1
    assert blocking_items[0].priority == ACTION_PRIORITY_P1


def test_build_action_queue_summary_review_required_item_has_p2_priority() -> None:
    # review_required category → p2 (same priority bucket as warning severity)
    escalation = _make_blocking_escalation()
    queue = build_action_queue_summary(escalation)

    review_items = [
        item for item in queue.items if item.queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    ]
    assert len(review_items) == 1
    assert review_items[0].priority == ACTION_PRIORITY_P2


def test_build_action_queue_summary_items_sorted_blocking_first() -> None:
    escalation = _make_blocking_escalation()
    queue = build_action_queue_summary(escalation)

    assert queue.items[0].queue_status == ACTION_QUEUE_STATUS_BLOCKING
    assert queue.items[-1].queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED


def test_build_action_queue_summary_to_json_dict_is_read_only() -> None:
    escalation = _make_blocking_escalation()
    payload = build_action_queue_summary(escalation).to_json_dict()

    assert payload["report_type"] == "action_queue_summary"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert "items" in payload
    assert len(payload["items"]) == 2


def test_build_blocking_actions_returns_only_blocking_items() -> None:
    queue = build_action_queue_summary(_make_blocking_escalation())
    blocking = build_blocking_actions(queue)

    assert blocking.blocking_count == 1
    assert blocking.queue_status == ACTION_QUEUE_STATUS_BLOCKING
    assert len(blocking.items) == 1
    assert blocking.items[0].queue_status == ACTION_QUEUE_STATUS_BLOCKING
    assert blocking.execution_enabled is False
    assert blocking.write_back_allowed is False


def test_build_blocking_actions_empty_when_no_blocking_items() -> None:
    escalation = build_operational_escalation_summary(
        _make_empty_readiness_report(),
        review_required_summary=_make_review_required_summary(),
    )
    queue = build_action_queue_summary(escalation)
    blocking = build_blocking_actions(queue)

    assert blocking.blocking_count == 0
    assert blocking.queue_status == ACTION_QUEUE_STATUS_CLEAR
    assert blocking.items == []


def test_build_prioritized_actions_returns_all_items_in_order() -> None:
    queue = build_action_queue_summary(_make_blocking_escalation())
    prioritized = build_prioritized_actions(queue)

    assert prioritized.action_count == 2
    assert prioritized.items == list(queue.items)
    assert prioritized.execution_enabled is False
    assert prioritized.write_back_allowed is False


def test_build_review_required_actions_returns_only_review_required_items() -> None:
    queue = build_action_queue_summary(_make_blocking_escalation())
    rr = build_review_required_actions(queue)

    assert rr.review_required_count == 1
    assert rr.queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    assert len(rr.items) == 1
    assert rr.items[0].queue_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    assert rr.execution_enabled is False
    assert rr.write_back_allowed is False


def test_build_review_required_actions_empty_when_no_review_items() -> None:
    readiness_report = replace(
        _make_empty_readiness_report(),
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_BLOCKING,
            blocking_count=1,
            warning_count=0,
            advisory_count=0,
            items=[
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_BLOCKING,
                    severity=SEVERITY_CRITICAL,
                    category="handoff_backlog",
                    summary="Blocking gate with no review items.",
                    subsystem="handoff",
                    blocking_reason="Backlog threshold breached.",
                    recommended_actions=[],
                    evidence_refs=[],
                )
            ],
        ),
    )
    escalation = build_operational_escalation_summary(readiness_report)
    queue = build_action_queue_summary(escalation)
    rr = build_review_required_actions(queue)

    assert rr.review_required_count == 0
    assert rr.queue_status == ACTION_QUEUE_STATUS_CLEAR
    assert rr.items == []


def test_action_queue_advisory_items_are_excluded_from_queue() -> None:
    """Advisory items (operator_action_required=False) MUST NOT appear in action queue."""
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
                    summary="Minor audit variance observed.",
                    subsystem="distribution",
                    recommended_actions=["Observe only."],
                    evidence_refs=[],
                )
            ],
        ),
    )
    escalation = build_operational_escalation_summary(readiness_report)
    queue = build_action_queue_summary(escalation)

    assert queue.total_count == 0
    assert queue.queue_status == ACTION_QUEUE_STATUS_CLEAR


def test_open_action_queue_status_when_only_warning_items() -> None:
    readiness_report = replace(
        _make_empty_readiness_report(),
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_WARNING,
            blocking_count=0,
            warning_count=1,
            advisory_count=0,
            items=[
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_WARNING,
                    severity="warning",
                    category="provider_health",
                    summary="Companion provider drift detected.",
                    subsystem="providers",
                    recommended_actions=["Inspect companion runtime."],
                    evidence_refs=["artifacts/provider_health.json"],
                )
            ],
        ),
    )
    escalation = build_operational_escalation_summary(readiness_report)
    queue = build_action_queue_summary(escalation)

    assert queue.total_count == 1
    assert queue.queue_status == ACTION_QUEUE_STATUS_OPEN
    assert queue.items[0].priority == ACTION_PRIORITY_P2
    assert queue.items[0].queue_status == ACTION_QUEUE_STATUS_OPEN


@pytest.mark.asyncio
async def test_get_mcp_capabilities_lists_canonical_action_queue_tools() -> None:
    from app.agents.mcp_server import get_mcp_capabilities

    payload = json.loads(await get_mcp_capabilities())
    assert "get_action_queue_summary" in payload["read_tools"]
    assert "get_blocking_actions" in payload["read_tools"]
    assert "get_prioritized_actions" in payload["read_tools"]
    assert "get_review_required_actions" in payload["read_tools"]




def _make_decision_pack_pair():
    """Return canonical summaries for decision-pack tests."""
    readiness_report = replace(
        _make_empty_readiness_report(),
        protective_gate_summary=ProtectiveGateSummary(
            gate_status=GATE_STATUS_BLOCKING,
            blocking_count=1,
            warning_count=0,
            advisory_count=0,
            items=[
                ProtectiveGateItem(
                    gate_status=GATE_STATUS_BLOCKING,
                    severity=SEVERITY_CRITICAL,
                    category="handoff_backlog",
                    summary="Pending handoffs exceeded the guarded threshold.",
                    subsystem="handoff",
                    blocking_reason="Consumer backlog is above the configured limit.",
                    recommended_actions=["Review pending handoffs."],
                    evidence_refs=["artifacts/handoffs.jsonl"],
                )
            ],
        ),
    )
    escalation = build_operational_escalation_summary(
        readiness_report,
        review_required_summary=_make_review_required_summary(),
    )
    blocking = build_blocking_summary(escalation)
    queue = build_action_queue_summary(escalation)
    return readiness_report, blocking, queue, _make_review_required_summary()


def test_build_operator_decision_pack_is_read_only() -> None:
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )

    assert pack.execution_enabled is False
    assert pack.write_back_allowed is False
    assert pack.interface_mode == "read_only"
    assert pack.report_type == "operator_decision_pack"


def test_build_operator_decision_pack_reflects_blocking_status() -> None:
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )

    assert pack.overall_status == GATE_STATUS_BLOCKING
    assert pack.blocking_count == 1
    assert pack.action_queue_count == 2
    assert pack.review_required_count == 1
    assert pack.readiness_summary == readiness_report
    assert pack.blocking_summary == blocking
    assert pack.action_queue_summary == queue
    assert pack.review_required_summary == review_required
    assert pack.affected_subsystems == ["handoff", "artifacts"]


def test_build_operator_decision_pack_is_clear_without_issues() -> None:
    readiness_report = _make_empty_readiness_report()
    escalation = build_operational_escalation_summary(readiness_report)
    blocking = build_blocking_summary(escalation)
    queue = build_action_queue_summary(escalation)
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
    )

    assert pack.overall_status == GATE_STATUS_CLEAR
    assert pack.blocking_count == 0
    assert pack.action_queue_count == 0
    assert pack.action_queue_summary == queue
    assert pack.action_queue_summary.items == []
    assert pack.operator_guidance == []
    assert pack.evidence_refs == []
    assert pack.review_required_summary is None
    assert pack.execution_enabled is False


def test_build_operator_decision_pack_embeds_queue_and_blocking_summaries() -> None:
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )

    assert list(pack.action_queue_summary.items) == list(queue.items)
    assert list(pack.blocking_summary.items) == list(blocking.items)


def test_build_operator_decision_pack_consolidates_guidance_and_evidence() -> None:
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )

    for ref in queue.evidence_refs:
        assert ref in pack.evidence_refs
    assert review_required.entries[0].path in pack.evidence_refs
    assert any("Review pending handoffs" in note for note in pack.operator_guidance)
    assert any(
        "Confirm the artifact type" in note for note in pack.operator_guidance
    )


def test_build_operator_decision_pack_to_json_dict_invariants() -> None:
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    payload = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    ).to_json_dict()

    assert payload["report_type"] == "operator_decision_pack"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert payload["overall_status"] == GATE_STATUS_BLOCKING
    assert payload["blocking_count"] == 1
    assert payload["review_required_count"] == 1
    assert payload["action_queue_count"] == 2
    assert payload["affected_subsystems"] == ["handoff", "artifacts"]
    assert "operator_guidance" in payload
    assert "evidence_refs" in payload
    assert "generated_at" in payload
    assert payload["readiness_summary"]["report_type"] == "operational_readiness"
    assert payload["blocking_summary"]["report_type"] == "blocking_summary"
    assert payload["action_queue_summary"]["report_type"] == "action_queue_summary"
    assert (
        payload["review_required_summary"]["report_type"]
        == "review_required_artifact_summary"
    )
    assert "pack_status" not in payload
    assert "action_items" not in payload


def test_save_operator_decision_pack_writes_valid_json(tmp_path: Path) -> None:
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )
    out_path = tmp_path / "reports" / "operator_decision_pack.json"

    result = save_operator_decision_pack(pack, out_path)

    assert result == out_path
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "operator_decision_pack"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


def test_operator_decision_pack_generated_at_is_iso_string() -> None:
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )

    assert isinstance(pack.generated_at, str)
    assert "T" in pack.generated_at


def test_build_operator_decision_pack_supports_partial_review_required_only() -> None:
    review_required = _make_review_required_summary()

    pack = build_operator_decision_pack(review_required_summary=review_required)

    assert pack.overall_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    assert pack.blocking_count == 0
    assert pack.review_required_count == 1
    assert pack.action_queue_count == 0
    assert pack.affected_subsystems == ["artifacts"]
    assert pack.readiness_summary is None
    assert pack.blocking_summary is None
    assert pack.action_queue_summary is None
    assert pack.review_required_summary == review_required


def test_build_operator_decision_pack_supports_empty_inputs() -> None:
    pack = build_operator_decision_pack()

    assert pack.overall_status == GATE_STATUS_CLEAR
    assert pack.blocking_count == 0
    assert pack.review_required_count == 0
    assert pack.action_queue_count == 0
    assert pack.affected_subsystems == []
    assert pack.operator_guidance == []
    assert pack.evidence_refs == []


@pytest.mark.asyncio
async def test_get_mcp_capabilities_lists_decision_pack_tool() -> None:
    from app.agents.mcp_server import get_mcp_capabilities

    payload = json.loads(await get_mcp_capabilities())
    assert "get_decision_pack_summary" in payload["read_tools"]
    assert (
        payload["aliases"]["get_operator_decision_pack"]["canonical_tool"]
        == "get_decision_pack_summary"
    )


def test_build_daily_operator_summary_projects_canonical_fields() -> None:
    now = datetime.now(UTC)
    today = now.isoformat()
    yesterday = (now - timedelta(days=1)).isoformat()

    summary = build_daily_operator_summary(
        readiness_summary={"readiness_status": "warning"},
        recent_cycles_summary={
            "recent_cycles": [
                {"status": "no_signal", "symbol": "ETH/USDT", "completed_at": yesterday},
                {"status": "risk_rejected", "symbol": "BTC/USDT", "completed_at": today},
            ]
        },
        portfolio_snapshot={"position_count": 2, "total_equity_usd": 10_000.0},
        exposure_summary={"gross_exposure_usd": 2_500.0, "mark_to_market_status": "ok"},
        decision_pack_summary={"overall_status": "warning"},
        review_journal_summary={"open_count": 3},
        now_utc=today,
    )

    payload = summary.to_json_dict()
    assert payload["report_type"] == "daily_operator_summary"
    assert payload["readiness_status"] == "warning"
    assert payload["cycle_count_today"] == 1
    assert payload["last_cycle_status"] == "risk_rejected"
    assert payload["last_cycle_symbol"] == "BTC/USDT"
    assert payload["position_count"] == 2
    assert payload["total_exposure_pct"] == 25.0
    assert payload["mark_to_market_status"] == "ok"
    assert payload["decision_pack_status"] == "warning"
    assert payload["open_incidents"] == 3
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert "trading" not in str(payload).lower()


def test_build_daily_operator_summary_fail_closed_on_partial_inputs() -> None:
    summary = build_daily_operator_summary(
        recent_cycles_summary={"recent_cycles": [{"status": "no_signal"}]},
    )
    payload = summary.to_json_dict()

    assert payload["report_type"] == "daily_operator_summary"
    assert payload["readiness_status"] == "unknown"
    assert payload["position_count"] == 0
    assert payload["total_exposure_pct"] == 0.0
    assert payload["decision_pack_status"] == "unknown"
    assert payload["open_incidents"] == 0
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


















@pytest.mark.asyncio
async def test_get_mcp_capabilities_lists_operator_runbook_tool() -> None:
    from app.agents.mcp_server import get_mcp_capabilities

    payload = json.loads(await get_mcp_capabilities())
    assert "get_operator_runbook" in payload["read_tools"]


# ---------------------------------------------------------------------------
# Sprint 30 — Operator Runbook Surface + Command Safety Guardrails
# ---------------------------------------------------------------------------


from app.research.operational_readiness import (  # noqa: E402
    build_operator_runbook,
    save_operator_runbook,
)


def test_build_operator_runbook_read_only_invariants() -> None:
    """Runbook stays read-only and advisory-only."""
    pack = build_operator_decision_pack()
    runbook = build_operator_runbook(decision_pack=pack)

    assert runbook.execution_enabled is False
    assert runbook.write_back_allowed is False
    assert runbook.auto_remediation_enabled is False
    assert runbook.auto_routing_enabled is False
    assert runbook.interface_mode == "read_only"
    assert runbook.report_type == "operator_runbook_summary"


def test_build_operator_runbook_from_blocking_pack() -> None:
    """Blocking pack yields ordered blocking and review-required next steps."""
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )
    runbook = build_operator_runbook(decision_pack=pack)

    assert runbook.blocking_count >= 1
    assert runbook.action_queue_count == 2
    assert len(runbook.steps) >= 1
    assert len(runbook.next_steps) >= 1
    blocking_steps = [s for s in runbook.steps if s.blocking]
    assert len(blocking_steps) >= 1
    assert all(s.priority == "p1" for s in blocking_steps)
    assert runbook.next_steps[0].blocking is True


def test_build_operator_runbook_step_ordering() -> None:
    """Steps stay in canonical queue order."""
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )
    runbook = build_operator_runbook(decision_pack=pack)

    priority_order = {"p1": 0, "p2": 1, "p3": 2}
    queue_order = {"blocking": 0, "review_required": 1, "open": 2, "clear": 3}
    orders = [
        (priority_order.get(step.priority, 99), queue_order.get(step.queue_status, 99))
        for step in runbook.steps
    ]
    assert orders == sorted(orders)
    assert [step.step_id for step in runbook.next_steps] == [
        step.step_id for step in runbook.steps[: len(runbook.next_steps)]
    ]


def test_build_operator_runbook_command_refs_match_registered_research_commands() -> None:
    """Runbook command refs must point only to real canonical research commands."""
    from app.cli.main import get_registered_research_command_names

    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )
    runbook = build_operator_runbook(decision_pack=pack)
    registered = get_registered_research_command_names()

    for step in runbook.steps:
        assert step.command_refs
        for ref in step.command_refs:
            parts = ref.split()
            assert len(parts) == 2
            assert parts[0] == "research"
            assert parts[1] in registered
            assert ref not in {
                "research governance-summary",
                "research operator-decision-pack",
            }
    assert "research decision-pack-summary" in runbook.command_refs
    assert "research governance-summary" not in runbook.command_refs


def test_build_operator_runbook_empty_pack_is_safe() -> None:
    """Empty decision pack produces a valid runbook with zero steps."""
    pack = build_operator_decision_pack()
    runbook = build_operator_runbook(decision_pack=pack)

    assert runbook.steps == []
    assert runbook.next_steps == []
    assert runbook.blocking_count == 0
    assert runbook.review_required_count == 0
    assert runbook.action_queue_count == 0
    assert runbook.command_refs == []
    assert runbook.overall_status == "clear"
    assert runbook.execution_enabled is False


def test_build_operator_runbook_partial_review_required_only() -> None:
    """Governance-only input still yields a safe review-required runbook."""
    pack = build_operator_decision_pack(review_required_summary=_make_review_required_summary())
    runbook = build_operator_runbook(decision_pack=pack)

    assert runbook.overall_status == ACTION_QUEUE_STATUS_REVIEW_REQUIRED
    assert runbook.blocking_count == 0
    assert runbook.review_required_count == 1
    assert runbook.action_queue_count == 0
    assert len(runbook.steps) == 1
    assert runbook.steps[0].queue_status == "review_required"
    assert "research review-required-summary" in runbook.steps[0].command_refs
    assert "trade" not in runbook.steps[0].summary.lower()


def test_save_operator_runbook_writes_json(tmp_path: Path) -> None:
    """save_operator_runbook persists valid JSON with expected fields."""
    pack = build_operator_decision_pack()
    runbook = build_operator_runbook(decision_pack=pack)
    out_path = tmp_path / "runbook.json"
    result = save_operator_runbook(runbook, out_path)

    assert result == out_path
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "operator_runbook_summary"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert "steps" in payload
    assert "next_steps" in payload
    assert "command_refs" in payload


def test_build_operator_runbook_has_no_trading_semantics() -> None:
    """Runbook content remains advisory and non-executable."""
    readiness_report, blocking, queue, review_required = _make_decision_pack_pair()
    pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking,
        action_queue_summary=queue,
        review_required_summary=review_required,
    )
    runbook = build_operator_runbook(decision_pack=pack)

    serialized = json.dumps(runbook.to_json_dict()).lower()
    assert "trade" not in serialized
    assert "order" not in serialized
    assert "execute" not in serialized


def test_research_help_exposes_operator_runbook_command() -> None:
    from typer.testing import CliRunner

    from app.cli.main import app

    result = CliRunner().invoke(app, ["research", "--help"])
    assert result.exit_code == 0, result.output
    assert "runbook-summary" in result.output
    assert "runbook-next-steps" in result.output
    assert "operator-runbook" in result.output


