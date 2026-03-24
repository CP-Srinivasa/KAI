"""Unit tests for the canonical Sprint-21 operational readiness summary."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.audit import (
    ALTER_AUDIT_JSONL_FILENAME,
    AlertAuditRecord,
    append_alert_audit,
    load_alert_audits,
)
from app.core.enums import AnalysisSource, SentimentLabel, SourceType
from app.research.abc_result import (
    ABCInferenceEnvelope,
    DistributionMetadata,
    PathResultEnvelope,
)
from app.research.active_route import ActiveRouteState
from app.research.artifact_lifecycle import (
    build_retention_report,
    build_review_required_summary,
)
from app.research.distribution import build_handoff_collector_summary
from app.research.execution_handoff import create_signal_handoff
from app.research.operational_readiness import (
    CATEGORY_ACKNOWLEDGEMENT_AUDIT,
    CATEGORY_ARTIFACT_STATE,
    CATEGORY_DISTRIBUTION_DRIFT,
    CATEGORY_HANDOFF_BACKLOG,
    CATEGORY_PROVIDER_HEALTH,
    CATEGORY_REVIEW_REQUIRED,
    CATEGORY_SHADOW_CONTROL_FAILURE,
    CATEGORY_STALE_STATE,
    OperationalArtifactRefs,
    build_blocking_summary,
    build_operational_escalation_summary,
    build_operational_readiness_report,
    build_operator_action_summary,
    save_operational_readiness_report,
)
from app.research.signals import extract_signal_candidates
from tests.unit.factories import make_document


def _make_handoff() -> object:
    document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        tickers=["BTC"],
        crypto_assets=[],
        relevance_score=0.93,
        credibility_score=0.88,
        provider="openai",
        analysis_source=AnalysisSource.EXTERNAL_LLM,
        source_name="CoinDesk",
        source_type=SourceType.RSS_FEED,
        summary="ETF demand remains elevated.",
    )
    signal = extract_signal_candidates([document], min_priority=8)[0]
    return create_signal_handoff(signal, document=document)


def test_append_alert_audit_creates_file(tmp_path: Path) -> None:
    record = AlertAuditRecord(
        document_id="doc-1",
        channel="email",
        message_id="msg-1",
        is_digest=True,
    )

    append_alert_audit(record, tmp_path)

    out_file = tmp_path / ALTER_AUDIT_JSONL_FILENAME
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8").strip())
    assert payload["document_id"] == "doc-1"
    assert payload["channel"] == "email"


def test_load_alert_audits_reads_records(tmp_path: Path) -> None:
    append_alert_audit(
        AlertAuditRecord("d1", "telegram", "m1", False),
        tmp_path,
    )
    append_alert_audit(
        AlertAuditRecord("d2", "email", "m2", True),
        tmp_path,
    )

    records = load_alert_audits(tmp_path)

    assert len(records) == 2
    assert records[0].document_id == "d1"
    assert records[1].is_digest is True


def test_build_operational_readiness_report_detects_pending_and_orphaned_acknowledgements() -> None:
    handoff = _make_handoff()
    collector_summary = build_handoff_collector_summary([handoff], [])
    collector_summary = collector_summary.__class__(
        total_handoffs=collector_summary.total_handoffs,
        acknowledged_count=collector_summary.acknowledged_count,
        pending_count=collector_summary.pending_count,
        ack_event_count=collector_summary.ack_event_count,
        consumers=collector_summary.consumers,
        acknowledged_handoffs=collector_summary.acknowledged_handoffs,
        pending_handoffs=collector_summary.pending_handoffs,
        orphaned_ack_count=2,
    )

    report = build_operational_readiness_report(
        handoffs=[handoff],
        collector_summary=collector_summary,
        artifacts=OperationalArtifactRefs(),
    )

    assert report.readiness_status == "warning"
    assert report.highest_severity == "warning"
    categories = {issue.category for issue in report.issues}
    assert CATEGORY_HANDOFF_BACKLOG in categories
    assert CATEGORY_ACKNOWLEDGEMENT_AUDIT in categories
    assert report.protective_gate_summary.gate_status == "warning"
    assert report.protective_gate_summary.blocking_count == 0
    assert report.protective_gate_summary.warning_count == 1
    assert report.protective_gate_summary.advisory_count == 1
    subsystems = {item.category: item.subsystem for item in report.protective_gate_summary.items}
    assert subsystems[CATEGORY_HANDOFF_BACKLOG] == "handoff"
    assert subsystems[CATEGORY_ACKNOWLEDGEMENT_AUDIT] == "handoff"


def test_build_operational_readiness_report_detects_missing_route_artifact_and_failures() -> None:
    handoff = _make_handoff()
    collector_summary = build_handoff_collector_summary([handoff], [])
    active_route = ActiveRouteState(
        profile_path="artifacts/routes/profile.json",
        profile_name="route-1",
        route_profile="primary_with_shadow_and_control",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=["B.companion"],
        control_path="C.rule",
        activated_at=(datetime.now(UTC) - timedelta(hours=30)).isoformat(),
        abc_envelope_output="artifacts/abc/missing.jsonl",
    )
    envelopes = [
        ABCInferenceEnvelope(
            document_id=handoff.document_id,
            route_profile="primary_with_shadow_and_control",
            primary_result=PathResultEnvelope(
                path_id="A.external_llm",
                provider="openai",
                analysis_source="external_llm",
            ),
            shadow_results=[
                PathResultEnvelope(
                    path_id="B.companion",
                    provider="companion",
                    analysis_source="internal",
                    summary="error: provider timeout",
                )
            ],
            control_result=PathResultEnvelope(
                path_id="C.rule",
                provider="rule",
                analysis_source="rule",
                summary="error: control unavailable",
            ),
            distribution_metadata=DistributionMetadata(
                route_profile="primary_with_shadow_and_control",
                active_primary_path="A.external_llm",
                distribution_targets=["abc_audit_jsonl"],
                activation_state="active",
            ),
        )
    ]

    report = build_operational_readiness_report(
        handoffs=[handoff],
        collector_summary=collector_summary,
        active_route_state=active_route,
        envelopes=envelopes,
        artifacts=OperationalArtifactRefs(
            abc_output=OperationalArtifactRefs().abc_output.__class__(
                path="artifacts/abc/missing.jsonl",
                present=False,
            ),
            active_route_state=OperationalArtifactRefs().active_route_state.__class__(
                path="artifacts/active_route_profile.json",
                present=True,
            ),
        ),
    )

    assert report.readiness_status == "critical"
    assert report.highest_severity == "critical"
    categories = {issue.category for issue in report.issues}
    assert CATEGORY_ARTIFACT_STATE in categories
    assert CATEGORY_PROVIDER_HEALTH in categories
    assert CATEGORY_SHADOW_CONTROL_FAILURE in categories
    assert CATEGORY_STALE_STATE in categories
    assert report.route_summary.shadow_failure_count == 1
    assert report.route_summary.control_failure_count == 1
    assert report.provider_health_summary.degraded_count == 2
    assert report.protective_gate_summary.gate_status == "blocking"
    assert report.protective_gate_summary.blocking_count == 1
    assert report.protective_gate_summary.warning_count >= 4
    assert all(item.recommended_actions for item in report.protective_gate_summary.items)
    statuses = {entry.path_id: entry.status for entry in report.provider_health_summary.entries}
    assert statuses["B.companion"] == "degraded"
    assert statuses["C.rule"] == "degraded"


def test_build_operational_readiness_report_detects_stale_pending_handoffs() -> None:
    handoff = replace(
        _make_handoff(),
        handoff_at=(datetime.now(UTC) - timedelta(hours=48)).isoformat(),
    )
    collector_summary = build_handoff_collector_summary([handoff], [])

    report = build_operational_readiness_report(
        handoffs=[handoff],
        collector_summary=collector_summary,
        stale_after_hours=24,
    )

    assert any(issue.category == CATEGORY_STALE_STATE for issue in report.issues)


def test_build_operational_readiness_report_detects_distribution_drift() -> None:
    handoff = replace(
        _make_handoff(),
        path_type="shadow",
        delivery_class="audit_only",
        consumer_visibility="hidden",
    )
    collector_summary = build_handoff_collector_summary([handoff], [])

    report = build_operational_readiness_report(
        handoffs=[handoff],
        collector_summary=collector_summary,
    )

    assert report.distribution_drift_summary.status == "critical"
    assert report.distribution_drift_summary.classification_mismatch_count == 1
    categories = {issue.category for issue in report.issues}
    assert CATEGORY_DISTRIBUTION_DRIFT in categories


def test_operational_readiness_report_to_json_dict_structure() -> None:
    handoff = _make_handoff()
    collector_summary = build_handoff_collector_summary([handoff], [])
    report = build_operational_readiness_report(
        handoffs=[handoff],
        collector_summary=collector_summary,
        alert_audits=[AlertAuditRecord("doc-1", "telegram", "m-1", False)],
    )

    payload = report.to_json_dict()

    assert payload["report_type"] == "operational_readiness"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert "collector_summary" in payload
    assert "route_summary" in payload
    assert "alert_dispatch_summary" in payload
    assert "provider_health_summary" in payload
    assert "distribution_drift_summary" in payload
    assert "protective_gate_summary" in payload
    assert payload["protective_gate_summary"]["interface_mode"] == "read_only"
    assert payload["protective_gate_summary"]["execution_enabled"] is False
    assert payload["protective_gate_summary"]["write_back_allowed"] is False
    assert isinstance(payload["issues"], list)


def test_save_operational_readiness_report_creates_file(tmp_path: Path) -> None:
    handoff = _make_handoff()
    collector_summary = build_handoff_collector_summary([handoff], [])
    report = build_operational_readiness_report(
        handoffs=[handoff],
        collector_summary=collector_summary,
    )

    path = save_operational_readiness_report(report, tmp_path / "readiness.json")

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["report_type"] == "operational_readiness"


def test_build_operational_escalation_summary_from_report(tmp_path: Path) -> None:
    handoff = _make_handoff()
    collector_summary = build_handoff_collector_summary([handoff], [])
    active_route = ActiveRouteState(
        profile_path="artifacts/routes/profile.json",
        profile_name="route-1",
        route_profile="primary_with_shadow_and_control",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=["B.companion"],
        control_path="C.rule",
        activated_at=(datetime.now(UTC) - timedelta(hours=30)).isoformat(),
        abc_envelope_output="artifacts/abc/missing.jsonl",
    )
    envelopes = [
        ABCInferenceEnvelope(
            document_id=handoff.document_id,
            route_profile="primary_with_shadow_and_control",
            primary_result=PathResultEnvelope(
                path_id="A.external_llm",
                provider="openai",
                analysis_source="external_llm",
            ),
            shadow_results=[
                PathResultEnvelope(
                    path_id="B.companion",
                    provider="companion",
                    analysis_source="internal",
                    summary="error: provider timeout",
                )
            ],
            control_result=PathResultEnvelope(
                path_id="C.rule",
                provider="rule",
                analysis_source="rule",
                summary="error: control unavailable",
            ),
            distribution_metadata=DistributionMetadata(
                route_profile="primary_with_shadow_and_control",
                active_primary_path="A.external_llm",
                distribution_targets=["abc_audit_jsonl"],
                activation_state="active",
            ),
        )
    ]

    report = build_operational_readiness_report(
        handoffs=[handoff],
        collector_summary=collector_summary,
        active_route_state=active_route,
        envelopes=envelopes,
        artifacts=OperationalArtifactRefs(
            abc_output=OperationalArtifactRefs().abc_output.__class__(
                path="artifacts/abc/missing.jsonl",
                present=False,
            ),
            active_route_state=OperationalArtifactRefs().active_route_state.__class__(
                path="artifacts/active_route_profile.json",
                present=True,
            ),
        ),
    )

    review_path = tmp_path / "manual_review_blob.json"
    review_path.write_text("{}", encoding="utf-8")
    review_required_summary = build_review_required_summary(
        build_retention_report(tmp_path, stale_after_days=30.0)
    )
    escalation = build_operational_escalation_summary(
        report,
        review_required_summary=review_required_summary,
    )
    blocking_summary = build_blocking_summary(escalation)
    operator_action_summary = build_operator_action_summary(escalation)

    assert escalation.escalation_status == "blocking"
    assert escalation.severity == "critical"
    assert escalation.blocking is True
    assert escalation.blocking_count == 1
    assert escalation.warning_count >= 1
    assert escalation.review_required_count == 1
    assert escalation.operator_action_count >= 2
    assert escalation.execution_enabled is False
    assert escalation.write_back_allowed is False
    assert any(
        item.category == CATEGORY_ARTIFACT_STATE and item.blocking for item in escalation.items
    )
    assert any(
        item.category == CATEGORY_REVIEW_REQUIRED and item.operator_action_required
        for item in escalation.items
    )
    assert str(review_path.name) in " ".join(escalation.evidence_refs)

    assert blocking_summary.blocking is True
    assert blocking_summary.blocking_count == 1
    assert len(blocking_summary.items) == 1
    assert blocking_summary.items[0].category == CATEGORY_ARTIFACT_STATE

    assert operator_action_summary.operator_action_count >= 2
    assert operator_action_summary.review_required_count == 1
    assert any(item.category == CATEGORY_REVIEW_REQUIRED for item in operator_action_summary.items)


def test_build_operational_escalation_summary_clean() -> None:
    handoff = _make_handoff()
    collector_summary = build_handoff_collector_summary([handoff], [])
    # Using a fully clean report
    report = build_operational_readiness_report(
        handoffs=[],
        collector_summary=collector_summary.__class__(
            total_handoffs=0,
            acknowledged_count=0,
            pending_count=0,
            ack_event_count=0,
            consumers=[],
            acknowledged_handoffs=[],
            pending_handoffs=[],
            orphaned_ack_count=0,
        ),
        artifacts=OperationalArtifactRefs(),
    )

    escalation = build_operational_escalation_summary(report)

    assert escalation.escalation_status == "clear"
    assert escalation.severity == "none"
    assert escalation.blocking is False
    assert escalation.blocking_count == 0
    assert escalation.warning_count == 0
    assert escalation.operator_action_count == 0
    assert len(escalation.items) == 0
