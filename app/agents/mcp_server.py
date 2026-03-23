from __future__ import annotations

import datetime
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.alerts.audit import load_alert_audits
from app.core.domain.document import CanonicalDocument
from app.core.settings import get_settings
from app.research.abc_result import load_abc_inference_envelopes
from app.research.active_route import (
    DEFAULT_ACTIVE_ROUTE_PATH,
    load_active_route_state,
)
from app.research.active_route import (
    activate_route_profile as persist_active_route_profile,
)
from app.research.active_route import (
    deactivate_route_profile as clear_active_route_profile,
)
from app.research.briefs import ResearchBriefBuilder
from app.research.distribution import (
    build_distribution_classification_report,
    build_execution_handoff_report,
    build_handoff_collector_summary,
    build_route_profile,
)
from app.research.execution_handoff import (
    HANDOFF_ACK_JSONL_FILENAME,
    append_handoff_acknowledgement_jsonl,
    create_handoff_acknowledgement,
    get_signal_handoff_by_id,
    load_handoff_acknowledgements,
    load_signal_handoffs,
)
from app.research.inference_profile import (
    InferenceRouteProfile,
    load_inference_route_profile,
    save_inference_route_profile,
)
from app.research.operational_readiness import (
    ArtifactRef,
    OperationalArtifactRefs,
    OperationalReadinessReport,
    build_operational_readiness_report,
)
from app.research.signals import SignalCandidate, extract_signal_candidates
from app.research.upgrade_cycle import build_upgrade_cycle_report
from app.research.watchlists import WatchlistRegistry, parse_watchlist_type
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

mcp = FastMCP("KAI Analyst Trading Bot")
logger = logging.getLogger(__name__)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACTS_SUBDIR = "artifacts"
_JSON_SUFFIXES = frozenset({".json"})
_ARTIFACT_SUFFIXES = frozenset({".json", ".jsonl"})
_HANDOFF_ACK_DEFAULT_PATH = f"artifacts/{HANDOFF_ACK_JSONL_FILENAME}"
_ALERT_AUDIT_DEFAULT_DIR = _ARTIFACTS_SUBDIR
_REVIEW_JOURNAL_DEFAULT_PATH = "artifacts/operator_review_journal.jsonl"
_PAPER_EXECUTION_AUDIT_DEFAULT_PATH = "artifacts/paper_execution_audit.jsonl"
_DECISION_JOURNAL_DEFAULT_PATH = "artifacts/decision_journal.jsonl"
_LOOP_AUDIT_DEFAULT_PATH = "artifacts/trading_loop_audit.jsonl"

_CANONICAL_MCP_READ_TOOL_NAMES = (
    "get_watchlists",
    "get_research_brief",
    "get_signal_candidates",
    "get_market_data_quote",
    "get_paper_portfolio_snapshot",
    "get_paper_positions_summary",
    "get_paper_exposure_summary",
    "get_narrative_clusters",
    "get_signals_for_execution",
    "get_distribution_classification_report",
    "get_route_profile_report",
    "get_inference_route_profile",
    "get_active_route_status",
    "get_upgrade_cycle_status",
    "get_handoff_collector_summary",
    "get_operational_readiness_summary",
    "get_provider_health",
    "get_distribution_drift",
    "get_protective_gate_summary",
    "get_remediation_recommendations",
    "get_artifact_inventory",
    "get_artifact_retention_report",
    "get_cleanup_eligibility_summary",
    "get_protected_artifact_summary",
    "get_review_required_summary",
    "get_escalation_summary",
    "get_blocking_summary",
    "get_operator_action_summary",
    "get_action_queue_summary",
    "get_blocking_actions",
    "get_prioritized_actions",
    "get_review_required_actions",
    "get_decision_pack_summary",
    "get_daily_operator_summary",
    "get_operator_runbook",
    "get_review_journal_summary",
    "get_resolution_summary",
    "get_alert_audit_summary",
    "get_decision_journal_summary",
    "get_trading_loop_status",
    "get_recent_trading_cycles",
)
_GUARDED_MCP_WRITE_TOOL_NAMES = (
    "create_inference_profile",
    "activate_route_profile",
    "deactivate_route_profile",
    "acknowledge_signal_handoff",
    "append_review_journal_entry",
    "append_decision_instance",
    "run_trading_loop_once",
)
_MCP_WORKFLOW_HELPER_NAMES = ("get_mcp_capabilities",)
_MCP_TOOL_ALIASES = {
    "get_handoff_summary": {
        "canonical_tool": "get_handoff_collector_summary",
        "tool_class": "read_only",
        "status": "compatibility_alias",
    },
    "get_operator_decision_pack": {
        "canonical_tool": "get_decision_pack_summary",
        "tool_class": "read_only",
        "status": "compatibility_alias",
    },
    "get_loop_cycle_summary": {
        "canonical_tool": "get_recent_trading_cycles",
        "tool_class": "read_only",
        "status": "compatibility_alias",
    },
}
_SUPERSEDED_MCP_TOOLS = {
    "get_operational_escalation_summary": {
        "replacement_tool": "get_escalation_summary",
        "tool_class": "read_only",
        "status": "superseded",
    }
}


def get_mcp_tool_inventory() -> dict[str, object]:
    """Return the canonical MCP inventory used by capabilities and contract tests."""
    return {
        "canonical_read_tools": list(_CANONICAL_MCP_READ_TOOL_NAMES),
        "guarded_write_tools": list(_GUARDED_MCP_WRITE_TOOL_NAMES),
        "workflow_helpers": list(_MCP_WORKFLOW_HELPER_NAMES),
        "aliases": {
            tool_name: dict(metadata)
            for tool_name, metadata in _MCP_TOOL_ALIASES.items()
        },
        "superseded_tools": {
            tool_name: dict(metadata)
            for tool_name, metadata in _SUPERSEDED_MCP_TOOLS.items()
        },
    }


def _resolve_workspace_path(
    path_value: str | Path,
    *,
    label: str,
    must_exist: bool = False,
    allowed_suffixes: frozenset[str] = _ARTIFACT_SUFFIXES,
) -> Path:
    candidate = Path(path_value)
    resolved = (candidate if candidate.is_absolute() else _WORKSPACE_ROOT / candidate).resolve()
    try:
        resolved.relative_to(_WORKSPACE_ROOT)
    except ValueError as err:
        raise ValueError(f"{label} must stay within workspace: {path_value}") from err

    if resolved.suffix.lower() not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValueError(f"{label} must use one of: {allowed}")

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{label} not found: {resolved}")

    return resolved


def _require_artifacts_subpath(resolved: Path, *, label: str) -> Path:
    """Ensure resolved path is inside workspace/artifacts/ (I-95: write guard)."""
    artifacts_root = _WORKSPACE_ROOT / _ARTIFACTS_SUBDIR
    try:
        resolved.relative_to(artifacts_root)
    except ValueError as err:
        raise ValueError(f"{label} must be within workspace/artifacts/: {resolved}") from err
    return resolved


def _resolve_workspace_dir(
    path_value: str | Path,
    *,
    label: str,
    must_exist: bool = False,
) -> Path:
    candidate = Path(path_value)
    resolved = (candidate if candidate.is_absolute() else _WORKSPACE_ROOT / candidate).resolve()
    try:
        resolved.relative_to(_WORKSPACE_ROOT)
    except ValueError as err:
        raise ValueError(f"{label} must stay within workspace: {path_value}") from err

    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"{label} must be a directory: {resolved}")

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{label} not found: {resolved}")

    return resolved


def _append_mcp_write_audit(
    *,
    tool: str,
    params: dict[str, object],
    result_summary: str,
) -> None:
    """Append a write audit entry to artifacts/mcp_write_audit.jsonl (I-94).

    Never raises â€” a failing audit must not suppress the original result.
    """
    audit_path = _WORKSPACE_ROOT / _ARTIFACTS_SUBDIR / "mcp_write_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.datetime.now(tz=datetime.UTC).isoformat(),
        "tool": tool,
        "params": params,
        "result_summary": result_summary,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _build_handoff_collector_report(
    *,
    handoff_path: str | Path,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
) -> tuple[dict[str, object], Path, Path]:
    resolved_handoff = _resolve_workspace_path(
        handoff_path,
        label="Signal handoff input",
        must_exist=True,
    )
    resolved_ack = _resolve_workspace_path(
        acknowledgement_path,
        label="Handoff acknowledgement audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    handoffs = load_signal_handoffs(resolved_handoff)
    acknowledgements = load_handoff_acknowledgements(resolved_ack)
    report = build_handoff_collector_summary(handoffs, acknowledgements)
    payload = report.to_json_dict()
    payload["handoff_path"] = str(resolved_handoff)
    payload["acknowledgement_path"] = str(resolved_ack)
    return payload, resolved_handoff, resolved_ack


def _build_operational_readiness_report(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> OperationalReadinessReport:
    resolved_handoff: Path | None = None
    handoffs = []
    if handoff_path is not None:
        resolved_handoff = _resolve_workspace_path(
            handoff_path,
            label="Signal handoff input",
        )
        if resolved_handoff.exists():
            handoffs = load_signal_handoffs(resolved_handoff)

    resolved_ack = _resolve_workspace_path(
        acknowledgement_path,
        label="Handoff acknowledgement audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    acknowledgements = load_handoff_acknowledgements(resolved_ack)
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = _resolve_workspace_path(
        state_path,
        label="Active route state",
        allowed_suffixes=_JSON_SUFFIXES,
    )
    active_route_state = load_active_route_state(resolved_state)

    resolved_abc: Path | None = None
    effective_abc_path = abc_output_path
    if effective_abc_path is None and active_route_state is not None:
        effective_abc_path = active_route_state.abc_envelope_output

    envelopes = []
    if effective_abc_path is not None:
        resolved_abc = _resolve_workspace_path(
            effective_abc_path,
            label="ABC envelope output",
            allowed_suffixes=frozenset({".json", ".jsonl"}),
        )
        if resolved_abc.exists():
            envelopes = load_abc_inference_envelopes(resolved_abc)

    resolved_alert_dir = _resolve_workspace_dir(
        alert_audit_dir,
        label="Alert audit directory",
    )
    alert_audits = load_alert_audits(resolved_alert_dir)

    return build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=envelopes,
        artifacts=OperationalArtifactRefs(
            handoff=ArtifactRef(
                path=(str(resolved_handoff) if resolved_handoff is not None else None),
                present=bool(resolved_handoff is not None and resolved_handoff.exists()),
            ),
            acknowledgements=ArtifactRef(
                path=str(resolved_ack),
                present=resolved_ack.exists(),
            ),
            active_route_state=ArtifactRef(
                path=str(resolved_state),
                present=resolved_state.exists(),
            ),
            abc_output=ArtifactRef(
                path=(str(resolved_abc) if resolved_abc is not None else None),
                present=bool(resolved_abc is not None and resolved_abc.exists()),
            ),
            alert_audit_dir=ArtifactRef(
                path=str(resolved_alert_dir),
                present=resolved_alert_dir.exists(),
            ),
        ),
        stale_after_hours=stale_after_hours,
    )


def _build_operational_readiness_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    return _build_operational_readiness_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    ).to_json_dict()


def _filter_readiness_issues(
    payload: dict[str, object],
    *,
    category: str,
) -> list[dict[str, object]]:
    issues = payload.get("issues", [])
    if not isinstance(issues, list):
        return []
    return [
        issue for issue in issues if isinstance(issue, dict) and issue.get("category") == category
    ]


def _build_provider_health_payload(
    *,
    handoff_path: str | Path | None = None,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
) -> dict[str, object]:
    readiness_payload = _build_operational_readiness_payload(
        handoff_path=handoff_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
    )
    summary = readiness_payload.get("provider_health_summary", {})
    if not isinstance(summary, dict):
        raise ValueError("Operational readiness payload is missing provider_health_summary")
    return {
        "report_type": "provider_health_summary",
        "derived_from": "operational_readiness",
        "generated_at": readiness_payload.get("generated_at"),
        "readiness_status": readiness_payload.get("readiness_status"),
        "highest_severity": readiness_payload.get("highest_severity"),
        **summary,
        "issues": _filter_readiness_issues(
            readiness_payload,
            category="provider_health",
        ),
    }


def _build_distribution_drift_payload(
    *,
    handoff_path: str | Path | None = None,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
) -> dict[str, object]:
    readiness_payload = _build_operational_readiness_payload(
        handoff_path=handoff_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
    )
    summary = readiness_payload.get("distribution_drift_summary", {})
    if not isinstance(summary, dict):
        raise ValueError("Operational readiness payload is missing distribution_drift_summary")
    return {
        "report_type": "distribution_drift_summary",
        "derived_from": "operational_readiness",
        "generated_at": readiness_payload.get("generated_at"),
        "readiness_status": readiness_payload.get("readiness_status"),
        "highest_severity": readiness_payload.get("highest_severity"),
        **summary,
        "issues": _filter_readiness_issues(
            readiness_payload,
            category="distribution_drift",
        ),
    }


def _build_protective_gate_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    readiness_payload = _build_operational_readiness_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )
    summary = readiness_payload.get("protective_gate_summary", {})
    if not isinstance(summary, dict):
        raise ValueError("Operational readiness payload is missing protective_gate_summary")
    return {
        "report_type": "protective_gate_summary",
        "derived_from": "operational_readiness",
        "generated_at": readiness_payload.get("generated_at"),
        "readiness_status": readiness_payload.get("readiness_status"),
        "highest_severity": readiness_payload.get("highest_severity"),
        **summary,
        "issues": readiness_payload.get("issues", []),
    }


def _build_remediation_recommendation_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    gate_payload = _build_protective_gate_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )
    raw_items = gate_payload.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError("Protective gate payload is missing items")

    recommendations: list[dict[str, object]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        raw_actions = item.get("recommended_actions", [])
        recommended_actions = list(raw_actions) if isinstance(raw_actions, list) else []
        raw_evidence = item.get("evidence_refs", [])
        evidence_refs = list(raw_evidence) if isinstance(raw_evidence, list) else []
        recommendations.append(
            {
                "gate_status": item.get("gate_status"),
                "severity": item.get("severity"),
                "category": item.get("category"),
                "summary": item.get("summary"),
                "subsystem": item.get("subsystem"),
                "blocking_reason": item.get("blocking_reason"),
                "recommended_actions": recommended_actions,
                "evidence_refs": evidence_refs,
            }
        )

    return {
        "report_type": "remediation_recommendation_report",
        "derived_from": "protective_gate_summary",
        "generated_at": gate_payload.get("generated_at"),
        "gate_status": gate_payload.get("gate_status"),
        "blocking_count": gate_payload.get("blocking_count", 0),
        "warning_count": gate_payload.get("warning_count", 0),
        "advisory_count": gate_payload.get("advisory_count", 0),
        "recommendation_count": len(recommendations),
        "recommendations": recommendations,
        "interface_mode": "read_only",
        "execution_enabled": False,
        "write_back_allowed": False,
    }


def _build_operational_escalation_report(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> Any:
    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )
    from app.research.operational_readiness import build_operational_escalation_summary

    readiness_report = _build_operational_readiness_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )
    resolved_dir = _resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = _resolve_workspace_path(state_path, label="state_path")
    retention_report = build_retention_report(
        resolved_dir,
        stale_after_days=retention_stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    review_required_summary = build_review_required_summary(retention_report)
    summary = build_operational_escalation_summary(
        readiness_report,
        review_required_summary=review_required_summary,
    )
    return summary


def _build_escalation_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, Any]:
    summary = _build_operational_escalation_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )
    return summary.to_json_dict()  # type: ignore[no-any-return]


def _build_blocking_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import build_blocking_summary

    escalation_summary = _build_operational_escalation_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )
    return build_blocking_summary(escalation_summary).to_json_dict()


def _build_operator_action_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import build_operator_action_summary

    escalation_summary = _build_operational_escalation_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )
    return build_operator_action_summary(escalation_summary).to_json_dict()


def _build_action_queue_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import build_action_queue_summary

    escalation_summary = _build_operational_escalation_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )
    return build_action_queue_summary(escalation_summary).to_json_dict()


def _build_blocking_actions_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_blocking_actions,
    )

    escalation_summary = _build_operational_escalation_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )
    queue_summary = build_action_queue_summary(escalation_summary)
    return build_blocking_actions(queue_summary).to_json_dict()


def _build_prioritized_actions_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_prioritized_actions,
    )

    escalation_summary = _build_operational_escalation_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )
    queue_summary = build_action_queue_summary(escalation_summary)
    return build_prioritized_actions(queue_summary).to_json_dict()


def _build_review_required_actions_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_review_required_actions,
    )

    escalation_summary = _build_operational_escalation_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )
    queue_summary = build_action_queue_summary(escalation_summary)
    return build_review_required_actions(queue_summary).to_json_dict()


async def _load_signal_candidates_and_documents(
    *,
    watchlist: str | None,
    min_priority: int,
    limit: int,
    provider: str | None = None,
) -> tuple[list[SignalCandidate], list[CanonicalDocument]]:
    settings = get_settings()
    registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))

    watchlist_boosts = None
    if watchlist:
        items = registry.get_watchlist(watchlist, item_type="assets")
        if items:
            watchlist_boosts = dict.fromkeys(items, 1)

    session_factory = build_session_factory(settings.db)
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        docs = await repo.list(is_analyzed=True, limit=limit * 5)

    if provider:
        normalized_provider = provider.strip().lower()
        docs = [
            document
            for document in docs
            if (document.provider or "").strip().lower() == normalized_provider
        ]

    candidates = extract_signal_candidates(
        docs,
        min_priority=min_priority,
        watchlist_boosts=watchlist_boosts,
    )
    return candidates[:limit], docs


@mcp.tool()
async def get_watchlists(watchlist_type: str = "assets") -> dict[str, list[str]]:
    """List available research watchlists or show the members of watchlists."""
    settings = get_settings()
    registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))
    resolved_type = parse_watchlist_type(watchlist_type)
    all_watchlists = registry.get_all_watchlists(item_type=resolved_type)
    return dict(all_watchlists)


@mcp.tool()
async def get_research_brief(
    watchlist: str, watchlist_type: str = "assets", limit: int = 100
) -> str:
    """Generate a research brief for a specific watchlist."""
    settings = get_settings()
    registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))
    resolved_type = parse_watchlist_type(watchlist_type)

    watchlist_items = registry.get_watchlist(watchlist, item_type=resolved_type)

    session_factory = build_session_factory(settings.db)
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        docs = await repo.list(is_analyzed=True, limit=limit * 5)

    if watchlist_items:
        docs = registry.filter_documents(docs, watchlist, item_type=resolved_type)

    docs = docs[:limit]
    builder = ResearchBriefBuilder(cluster_name=watchlist)
    brief = builder.build(docs)
    return brief.to_markdown()


@mcp.tool()
async def get_signal_candidates(
    watchlist: str | None = None, min_priority: int = 8, limit: int = 50
) -> str:
    """Generate actionable signal candidates from analyzed documents."""
    candidates, _docs = await _load_signal_candidates_and_documents(
        watchlist=watchlist,
        min_priority=min_priority,
        limit=limit,
    )
    return json.dumps([c.to_json_dict() for c in candidates], indent=2)


@mcp.tool()
async def get_market_data_quote(
    symbol: str = "BTC/USDT",
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Return one read-only market data quote snapshot from the canonical adapter path."""
    from app.market_data.service import get_market_data_snapshot

    snapshot = await get_market_data_snapshot(
        symbol=symbol,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return snapshot.to_json_dict()


async def _build_paper_portfolio_snapshot(
    *,
    audit_path: str = _PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> Any:
    from app.execution.portfolio_read import build_portfolio_snapshot

    resolved = _resolve_workspace_path(
        audit_path,
        label="Paper execution audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    return await build_portfolio_snapshot(
        audit_path=resolved,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
async def get_paper_portfolio_snapshot(
    audit_path: str = _PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Return canonical read-only paper portfolio snapshot from audit replay."""
    snapshot = await _build_paper_portfolio_snapshot(
        audit_path=audit_path,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return snapshot.to_json_dict()  # type: ignore[no-any-return]


@mcp.tool()
async def get_paper_positions_summary(
    audit_path: str = _PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Return positions-only slice from canonical paper portfolio snapshot."""
    from app.execution.portfolio_read import build_positions_summary

    snapshot = await _build_paper_portfolio_snapshot(
        audit_path=audit_path,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return build_positions_summary(snapshot)


@mcp.tool()
async def get_paper_exposure_summary(
    audit_path: str = _PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Return exposure-only slice from canonical paper portfolio snapshot."""
    from app.execution.portfolio_read import build_exposure_summary

    snapshot = await _build_paper_portfolio_snapshot(
        audit_path=audit_path,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return build_exposure_summary(snapshot)


@mcp.tool()
async def get_narrative_clusters(
    min_priority: int = 8,
    limit: int = 200,
    min_cluster_size: int = 2,
    merge_threshold: float = 0.30,
    max_clusters: int = 20,
    merge: bool = False,
) -> dict[str, object]:
    """Group active signal candidates into narrative clusters by asset Jaccard similarity.

    Pure read-only projection — no DB writes, no routing changes (I-184).
    Returns cluster summaries with velocity, acceleration, and dominant direction.
    """
    from app.analysis.narratives.cluster import ClusterConfig, NarrativeClusterEngine

    settings = get_settings()
    session_factory = build_session_factory(settings.db)
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        docs = await repo.list(is_analyzed=True, limit=limit)

    candidates = extract_signal_candidates(docs, min_priority=min_priority)

    config = ClusterConfig(
        min_cluster_size=min_cluster_size,
        merge_threshold=merge_threshold,
        max_clusters=max_clusters,
    )
    engine = NarrativeClusterEngine(config)
    clusters = engine.cluster(candidates)

    if merge:
        clusters = engine.merge_clusters(clusters)

    return {
        "report_type": "narrative_cluster_report",
        "execution_enabled": False,  # I-180
        "write_back_allowed": False,
        "candidate_count": len(candidates),
        "cluster_count": len(clusters),
        "config": {
            "min_cluster_size": min_cluster_size,
            "merge_threshold": merge_threshold,
            "max_clusters": max_clusters,
            "merge": merge,
        },
        "clusters": [cl.to_json_dict() for cl in clusters],
    }


@mcp.tool()
async def get_signals_for_execution(
    watchlist: str | None = None,
    min_priority: int = 8,
    limit: int = 50,
    provider: str | None = None,
) -> dict[str, object]:
    """Return a read-only external-consumption handoff for qualified signals."""
    candidates, docs = await _load_signal_candidates_and_documents(
        watchlist=watchlist,
        min_priority=min_priority,
        limit=limit,
        provider=provider,
    )
    report = build_execution_handoff_report(candidates, docs)
    return report.to_json_dict()


@mcp.tool()
async def get_distribution_classification_report(
    abc_output_path: str,
    watchlist: str | None = None,
    min_priority: int = 8,
    limit: int = 50,
    provider: str | None = None,
) -> dict[str, object]:
    """Read a route-aware distribution report from existing ABC audit envelopes only."""
    resolved = _resolve_workspace_path(
        abc_output_path,
        label="ABC envelope output",
        must_exist=True,
    )
    envelopes = load_abc_inference_envelopes(resolved)
    candidates, docs = await _load_signal_candidates_and_documents(
        watchlist=watchlist,
        min_priority=min_priority,
        limit=limit,
        provider=provider,
    )
    report = build_distribution_classification_report(candidates, docs, envelopes)
    payload = report.to_json_dict()
    payload["abc_output_path"] = str(resolved)
    return payload


@mcp.tool()
async def get_route_profile_report(limit: int = 1000) -> dict[str, object]:
    """Build the current route/distribution report from stored analyzed documents."""
    settings = get_settings()
    session_factory = build_session_factory(settings.db)
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        report = await build_route_profile(repo, limit=limit)
    return report.to_json_dict()


@mcp.tool()
async def get_inference_route_profile(profile_path: str) -> dict[str, object]:
    """Load a saved inference route profile from a workspace-local JSON file."""
    resolved = _resolve_workspace_path(
        profile_path,
        label="Inference route profile",
        must_exist=True,
        allowed_suffixes=_JSON_SUFFIXES,
    )
    profile = load_inference_route_profile(resolved)
    payload = profile.to_json_dict()
    payload["path"] = str(resolved)
    return payload


@mcp.tool()
async def get_active_route_status(
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Read the current active route state without changing routing or providers."""
    resolved = _resolve_workspace_path(
        state_path,
        label="Active route state",
        allowed_suffixes=_JSON_SUFFIXES,
    )
    state = load_active_route_state(resolved)
    if state is None:
        return {
            "active": False,
            "state_path": str(resolved),
        }
    return {
        "active": True,
        "state_path": str(resolved),
        "state": state.to_dict(),
    }


@mcp.tool()
async def get_upgrade_cycle_status(
    teacher_dataset_path: str,
    training_job_record_path: str | None = None,
    evaluation_report_path: str | None = None,
    comparison_report_path: str | None = None,
    promotion_record_path: str | None = None,
) -> dict[str, object]:
    """Summarize upgrade-cycle status from existing workspace-local artifacts only."""
    teacher_path = _resolve_workspace_path(
        teacher_dataset_path,
        label="Teacher dataset",
        must_exist=True,
    )
    training_path = (
        _resolve_workspace_path(
            training_job_record_path,
            label="Training job record",
            must_exist=True,
        )
        if training_job_record_path is not None
        else None
    )
    evaluation_path = (
        _resolve_workspace_path(
            evaluation_report_path,
            label="Evaluation report",
            must_exist=True,
        )
        if evaluation_report_path is not None
        else None
    )
    comparison_path = (
        _resolve_workspace_path(
            comparison_report_path,
            label="Comparison report",
            must_exist=True,
        )
        if comparison_report_path is not None
        else None
    )
    promotion_path = (
        _resolve_workspace_path(
            promotion_record_path,
            label="Promotion record",
            must_exist=True,
        )
        if promotion_record_path is not None
        else None
    )

    report = build_upgrade_cycle_report(
        teacher_path,
        training_job_record_path=training_path,
        evaluation_report_path=evaluation_path,
        comparison_report_path=comparison_path,
        promotion_record_path=promotion_path,
    )
    return report.to_json_dict()


@mcp.tool()
async def create_inference_profile(
    profile_name: str,
    route_profile: str,
    primary_path: str = "A.external_llm",
    shadow_paths: list[str] | None = None,
    control_path: str | None = None,
    output_path: str = "inference_route_profile.json",
    notes: list[str] | None = None,
) -> dict[str, object]:
    """Create an inference route profile JSON inside the workspace only."""
    resolved_output = _resolve_workspace_path(
        output_path,
        label="Inference route profile output",
        allowed_suffixes=_JSON_SUFFIXES,
    )
    _require_artifacts_subpath(resolved_output, label="Inference route profile output")
    profile = InferenceRouteProfile(
        profile_name=profile_name,
        route_profile=route_profile,
        active_primary_path=primary_path,
        enabled_shadow_paths=list(shadow_paths or []),
        control_path=control_path,
        notes=list(notes or []),
    )
    saved = save_inference_route_profile(profile, resolved_output)
    _append_mcp_write_audit(
        tool="create_inference_profile",
        params={
            "profile_name": profile_name,
            "route_profile": route_profile,
            "output_path": str(saved),
        },
        result_summary=f"saved: {saved}",
    )
    return {
        "output_path": str(saved),
        "profile": profile.to_json_dict(),
    }


@mcp.tool()
async def activate_route_profile(
    profile_path: str,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_envelope_output: str | None = None,
) -> dict[str, object]:
    """Activate an existing route profile via ActiveRouteState only."""
    resolved_profile = _resolve_workspace_path(
        profile_path,
        label="Inference route profile",
        must_exist=True,
        allowed_suffixes=_JSON_SUFFIXES,
    )
    resolved_state = _resolve_workspace_path(
        state_path,
        label="Active route state output",
        allowed_suffixes=_JSON_SUFFIXES,
    )
    _require_artifacts_subpath(resolved_state, label="Active route state output")
    resolved_abc_output = (
        _resolve_workspace_path(
            abc_envelope_output,
            label="ABC envelope output",
            allowed_suffixes=frozenset({".jsonl"}),
        )
        if abc_envelope_output is not None
        else None
    )
    if resolved_abc_output is not None:
        _require_artifacts_subpath(resolved_abc_output, label="ABC envelope output")
    state = persist_active_route_profile(
        profile_path=resolved_profile,
        state_path=resolved_state,
        abc_envelope_output=(str(resolved_abc_output) if resolved_abc_output is not None else None),
    )
    _append_mcp_write_audit(
        tool="activate_route_profile",
        params={
            "profile_path": str(resolved_profile),
            "state_path": str(resolved_state),
            "abc_envelope_output": (
                str(resolved_abc_output) if resolved_abc_output is not None else None
            ),
        },
        result_summary=f"activated: {state.route_profile}",
    )
    return {
        "state_path": str(resolved_state),
        "state": state.to_dict(),
        "app_llm_provider_unchanged": True,  # I-91, I-97
    }


@mcp.tool()
async def deactivate_route_profile(
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Deactivate the guarded route state file only."""
    resolved_state = _resolve_workspace_path(
        state_path,
        label="Active route state",
        allowed_suffixes=_JSON_SUFFIXES,
    )
    _require_artifacts_subpath(resolved_state, label="Active route state")
    removed = clear_active_route_profile(resolved_state)
    _append_mcp_write_audit(
        tool="deactivate_route_profile",
        params={"state_path": str(resolved_state)},
        result_summary=f"deactivated: {removed}",
    )
    return {
        "deactivated": removed,
        "state_path": str(resolved_state),
    }


@mcp.tool()
async def acknowledge_signal_handoff(
    handoff_path: str,
    handoff_id: str,
    consumer_agent_id: str,
    notes: str = "",
    acknowledgement_output_path: str = _HANDOFF_ACK_DEFAULT_PATH,
) -> dict[str, object]:
    """Append an audit-only acknowledgement for an existing visible SignalHandoff.

    Acknowledgement is AUDIT ONLY â€” not an execution trigger, not an approval,
    and not a routing decision (I-117, I-121, I-122).
    No write-back to KAI core DB (I-118).
    """
    resolved_handoff = _resolve_workspace_path(
        handoff_path,
        label="Signal handoff input",
        must_exist=True,
    )
    resolved_ack = _resolve_workspace_path(
        acknowledgement_output_path,
        label="Consumer acknowledgement audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    _require_artifacts_subpath(
        resolved_ack,
        label="Consumer acknowledgement audit",
    )
    handoffs = load_signal_handoffs(resolved_handoff)
    handoff = get_signal_handoff_by_id(handoffs, handoff_id)

    if handoff.consumer_visibility != "visible":
        raise PermissionError(
            f"Only consumer-visible handoffs can be acknowledged â€” "
            f"handoff {handoff.handoff_id!r} has "
            f"consumer_visibility={handoff.consumer_visibility!r}."
        )

    ack = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id=consumer_agent_id,
        notes=notes,
    )
    append_handoff_acknowledgement_jsonl(ack, resolved_ack)

    _append_mcp_write_audit(
        tool="acknowledge_signal_handoff",
        params={
            "handoff_path": str(resolved_handoff),
            "handoff_id": handoff_id,
            "consumer_agent_id": consumer_agent_id,
            "notes": notes,
            "acknowledgement_output_path": str(resolved_ack),
        },
        result_summary=f"acknowledged handoff {handoff_id} by {consumer_agent_id}",
    )

    return {
        "status": "acknowledged_in_audit_only",
        "handoff_id": ack.handoff_id,
        "signal_id": ack.signal_id,
        "consumer_agent_id": ack.consumer_agent_id,
        "handoff_path": str(resolved_handoff),
        "acknowledgement_path": str(resolved_ack),
        "acknowledgement": ack.to_json_dict(),
        "core_state_unchanged": True,
        "execution_enabled": False,
        "write_back_allowed": False,
    }


@mcp.tool()
async def get_handoff_collector_summary(
    handoff_path: str,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
) -> dict[str, object]:
    """Summarize pending and acknowledged handoffs from existing audit artifacts only."""
    payload, _resolved_handoff, _resolved_ack = _build_handoff_collector_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
    )
    return payload


@mcp.tool()
async def get_handoff_summary(
    handoff_path: str,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
) -> dict[str, object]:
    """Backward-compatible alias for the canonical collector summary surface."""
    payload, _resolved_handoff, _resolved_ack = _build_handoff_collector_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
    )
    return payload


@mcp.tool()
async def get_operational_readiness_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Build a read-only operational readiness summary from existing artifacts only."""
    return _build_operational_readiness_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )


@mcp.tool()
async def get_mcp_capabilities() -> str:
    """Return the MCP surface and the intentionally denied action classes."""
    inventory = get_mcp_tool_inventory()
    return json.dumps(
        {
            "status": "online",
            "description": "KAI Analyst Trading Bot - controlled MCP interface",
            "transport": "stdio_only",
            "read_tools": inventory["canonical_read_tools"],
            "write_tools": inventory["guarded_write_tools"],
            "guarded_write_tools": inventory["guarded_write_tools"],
            "workflow_helpers": inventory["workflow_helpers"],
            "aliases": inventory["aliases"],
            "superseded_tools": inventory["superseded_tools"],
            "guardrails": [
                "Write paths restricted to workspace/artifacts/ (I-95)",
                "Write audit JSONL appended for every write call (I-94)",
                "No APP_LLM_PROVIDER mutation",
                "No auto-routing or auto-promotion",
                "No direct execution hook for signals",
                "Trading loop control is explicit run-once only (no daemon/autopilot)",
                "Acknowledgement is audit-only â€” not write-back or execution trigger (I-116)",
                "Readiness summary is observational only â€” no auto-remediation",
                "Protective Gates are entirely read-only and advisory (I-123)",
                "Cleanup eligibility is advisory only â€” no auto-deletion",
                "No trading execution",
            ],
        },
        indent=2,
    )


@mcp.tool()
async def get_provider_health(
    handoff_path: str | None = None,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
) -> dict[str, object]:
    """Return the readiness-derived provider health slice only.

    This is a bounded read view over the canonical operational readiness stack,
    not a second monitoring implementation.
    """
    return _build_provider_health_payload(
        handoff_path=handoff_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
    )


@mcp.tool()
async def get_distribution_drift(
    handoff_path: str | None = None,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
) -> dict[str, object]:
    """Return the readiness-derived distribution drift slice only."""
    return _build_distribution_drift_payload(
        handoff_path=handoff_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
    )


@mcp.tool()
async def get_protective_gate_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Return the readiness-derived protective gate view only."""
    return _build_protective_gate_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )


@mcp.tool()
async def get_remediation_recommendations(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Return read-only remediation hints derived from protective gate items."""
    return _build_remediation_recommendation_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )


@mcp.tool()
async def get_artifact_inventory(
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return a read-only inventory of managed artifact files (I-149).

    Scans the artifacts directory and reports file age, size, and stale status.
    execution_enabled is always False (I-150). No filesystem writes.
    """
    from app.research.artifact_lifecycle import build_artifact_inventory

    resolved_dir = _resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    report = build_artifact_inventory(resolved_dir, stale_after_days=stale_after_days)
    return report.to_json_dict()


@mcp.tool()
async def get_artifact_retention_report(
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Return read-only artifact retention classification (I-153â€“I-161, Sprint 25).

    Classifies each artifact as protected, rotatable, or review_required.
    No filesystem mutations. execution_enabled and write_back_allowed are always False.
    delete_eligible_count is always 0 â€” deletion is never platform-initiated (I-154).
    """
    from app.research.artifact_lifecycle import build_retention_report

    resolved_dir = _resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = _resolve_workspace_path(state_path, label="state_path")
    active_route_active = resolved_state.exists()

    report = build_retention_report(
        resolved_dir,
        stale_after_days=stale_after_days,
        active_route_active=active_route_active,
    )
    return report.to_json_dict()


@mcp.tool()
async def get_cleanup_eligibility_summary(
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Return cleanup/archive eligibility derived from the canonical retention report."""
    from app.research.artifact_lifecycle import (
        build_cleanup_eligibility_summary,
        build_retention_report,
    )

    resolved_dir = _resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = _resolve_workspace_path(state_path, label="state_path")
    report = build_retention_report(
        resolved_dir,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    return build_cleanup_eligibility_summary(report).to_json_dict()


@mcp.tool()
async def get_protected_artifact_summary(
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Return the protected-artifact slice derived from the canonical retention report."""
    from app.research.artifact_lifecycle import (
        build_protected_artifact_summary,
        build_retention_report,
    )

    resolved_dir = _resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = _resolve_workspace_path(state_path, label="state_path")
    report = build_retention_report(
        resolved_dir,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    return build_protected_artifact_summary(report).to_json_dict()


@mcp.tool()
async def get_review_required_summary(
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Return the review-required slice derived from the canonical retention report (Sprint 26)."""
    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )

    resolved_dir = _resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = _resolve_workspace_path(state_path, label="state_path")
    report = build_retention_report(
        resolved_dir,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    return build_review_required_summary(report).to_json_dict()


@mcp.tool()
async def get_operational_escalation_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Return the operator-facing escalation summary derived from the canonical readiness report.

    Read-only surface (Sprint 27). No execution, no write-back, no auto-remediation.
    escalation_status: nominal / elevated / critical.
    I-169–I-176.
    """
    return _build_escalation_summary_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )


@mcp.tool()
async def get_escalation_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the canonical safe operational escalation summary."""
    return _build_escalation_summary_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


@mcp.tool()
async def get_blocking_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the blocking-only slice of the canonical escalation surface."""
    return _build_blocking_summary_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


@mcp.tool()
async def get_operator_action_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the operator-action-required slice of the canonical escalation surface."""
    return _build_operator_action_summary_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


@mcp.tool()
async def get_action_queue_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the canonical safe operator action queue derived from escalation only."""
    return _build_action_queue_summary_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


@mcp.tool()
async def get_blocking_actions(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the blocking-only slice of the canonical operator action queue."""
    return _build_blocking_actions_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


@mcp.tool()
async def get_prioritized_actions(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the operator action queue in derived priority order only."""
    return _build_prioritized_actions_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


@mcp.tool()
async def get_review_required_actions(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return review-required items from the canonical operator action queue only."""
    return _build_review_required_actions_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


def _build_operator_decision_pack_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_blocking_summary,
        build_operational_escalation_summary,
        build_operator_decision_pack,
    )

    readiness_report = _build_operational_readiness_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )
    resolved_dir = _resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = _resolve_workspace_path(state_path, label="state_path")
    retention_report = build_retention_report(
        resolved_dir,
        stale_after_days=retention_stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    review_required_summary = build_review_required_summary(retention_report)
    escalation_summary = build_operational_escalation_summary(
        readiness_report,
        review_required_summary=review_required_summary,
    )
    blocking_summary = build_blocking_summary(escalation_summary)
    action_queue_summary = build_action_queue_summary(escalation_summary)
    return build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking_summary,
        action_queue_summary=action_queue_summary,
        review_required_summary=review_required_summary,
    ).to_json_dict()


def _build_operator_runbook_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.cli.main import (
        extract_runbook_command_refs,
        get_invalid_research_command_refs,
    )
    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_blocking_summary,
        build_operational_escalation_summary,
        build_operator_decision_pack,
        build_operator_runbook,
    )

    readiness_report = _build_operational_readiness_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )
    resolved_dir = _resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = _resolve_workspace_path(state_path, label="state_path")
    retention_report = build_retention_report(
        resolved_dir,
        stale_after_days=retention_stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    review_required_summary = build_review_required_summary(retention_report)
    escalation_summary = build_operational_escalation_summary(
        readiness_report,
        review_required_summary=review_required_summary,
    )
    blocking_summary = build_blocking_summary(escalation_summary)
    action_queue_summary = build_action_queue_summary(escalation_summary)
    decision_pack = build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking_summary,
        action_queue_summary=action_queue_summary,
        review_required_summary=review_required_summary,
    )
    payload = build_operator_runbook(decision_pack=decision_pack).to_json_dict()
    invalid_refs = get_invalid_research_command_refs(
        extract_runbook_command_refs(payload)
    )
    if invalid_refs:
        raise ValueError(
            "Operator runbook contains invalid research command references: "
            + ", ".join(invalid_refs)
        )
    return payload


@mcp.tool()
async def get_decision_pack_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the canonical read-only operator decision pack summary.

    Bundles readiness status, escalation, action queue, and governance snapshots
    into a single situation-awareness surface. Advisory only — no execution
    authority. Decision pack is a derived snapshot; sub-report surfaces remain
    the source of truth. I-185–I-192.
    """
    return _build_operator_decision_pack_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def _safe_daily_surface_load(
    *,
    source_name: str,
    loader: Callable[[], Awaitable[dict[str, object]]],
) -> dict[str, object] | None:
    try:
        payload = await loader()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "daily_operator_summary degraded: %s unavailable (%s)",
            source_name,
            exc.__class__.__name__,
        )
        return None
    if not isinstance(payload, dict):
        logger.warning(
            "daily_operator_summary degraded: %s returned non-dict payload",
            source_name,
        )
        return None
    return payload


@mcp.tool()
async def get_daily_operator_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
    loop_audit_path: str = _LOOP_AUDIT_DEFAULT_PATH,
    loop_last_n: int = 50,
    portfolio_audit_path: str = _PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    market_data_provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    review_journal_path: str = _REVIEW_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Return one canonical daily operator aggregate from existing read surfaces only."""
    from app.research.operational_readiness import build_daily_operator_summary

    readiness_summary = await _safe_daily_surface_load(
        source_name="readiness_summary",
        loader=lambda: get_operational_readiness_summary(
            handoff_path=handoff_path,
            acknowledgement_path=acknowledgement_path,
            state_path=state_path,
            abc_output_path=abc_output_path,
            alert_audit_dir=alert_audit_dir,
            stale_after_hours=stale_after_hours,
        ),
    )
    recent_cycles_summary = await _safe_daily_surface_load(
        source_name="recent_cycles",
        loader=lambda: get_recent_trading_cycles(
            audit_path=loop_audit_path,
            last_n=loop_last_n,
        ),
    )
    portfolio_snapshot = await _safe_daily_surface_load(
        source_name="portfolio_snapshot",
        loader=lambda: get_paper_portfolio_snapshot(
            audit_path=portfolio_audit_path,
            provider=market_data_provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        ),
    )
    exposure_summary = await _safe_daily_surface_load(
        source_name="exposure_summary",
        loader=lambda: get_paper_exposure_summary(
            audit_path=portfolio_audit_path,
            provider=market_data_provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        ),
    )
    decision_pack_summary = await _safe_daily_surface_load(
        source_name="decision_pack_summary",
        loader=lambda: get_decision_pack_summary(
            handoff_path=handoff_path,
            acknowledgement_path=acknowledgement_path,
            state_path=state_path,
            abc_output_path=abc_output_path,
            alert_audit_dir=alert_audit_dir,
            stale_after_hours=stale_after_hours,
            artifacts_dir=artifacts_dir,
            retention_stale_after_days=retention_stale_after_days,
        ),
    )
    review_journal_summary = await _safe_daily_surface_load(
        source_name="review_journal_summary",
        loader=lambda: get_review_journal_summary(
            journal_path=review_journal_path,
        ),
    )

    summary = build_daily_operator_summary(
        readiness_summary=readiness_summary,
        recent_cycles_summary=recent_cycles_summary,
        portfolio_snapshot=portfolio_snapshot,
        exposure_summary=exposure_summary,
        decision_pack_summary=decision_pack_summary,
        review_journal_summary=review_journal_summary,
    )
    return summary.to_json_dict()


@mcp.tool()
async def get_operator_decision_pack(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Backward-compatible alias for the canonical decision-pack summary."""
    return _build_operator_decision_pack_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


@mcp.tool()
async def get_operator_runbook(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the canonical read-only operator runbook with validated commands."""
    return _build_operator_runbook_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


def _build_review_journal_summary_payload(
    *,
    journal_path: str | Path = _REVIEW_JOURNAL_DEFAULT_PATH,
) -> tuple[dict[str, object], Path]:
    from app.research.operational_readiness import (
        build_review_journal_summary,
        load_review_journal_entries,
    )

    resolved = _resolve_workspace_path(
        journal_path,
        label="Review journal path",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    entries = load_review_journal_entries(resolved)
    payload = build_review_journal_summary(
        entries,
        journal_path=resolved,
    ).to_json_dict()
    return payload, resolved


@mcp.tool()
async def append_review_journal_entry(
    source_ref: str,
    operator_id: str,
    review_action: str,
    review_note: str,
    evidence_refs: list[str] | None = None,
    journal_output_path: str = _REVIEW_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Append an operator review journal entry without mutating core operator state."""
    from app.research.operational_readiness import (
        append_review_journal_entry_jsonl,
        create_review_journal_entry,
    )

    resolved = _resolve_workspace_path(
        journal_output_path,
        label="Review journal output",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    _require_artifacts_subpath(resolved, label="Review journal output")

    entry = create_review_journal_entry(
        source_ref=source_ref,
        operator_id=operator_id,
        review_action=review_action,
        review_note=review_note,
        evidence_refs=evidence_refs,
    )
    append_review_journal_entry_jsonl(entry, resolved)

    _append_mcp_write_audit(
        tool="append_review_journal_entry",
        params={
            "source_ref": source_ref,
            "operator_id": operator_id,
            "review_action": review_action,
            "review_note": review_note,
            "evidence_refs": list(evidence_refs or []),
            "journal_output_path": str(resolved),
        },
        result_summary=f"review_journal entry {entry.review_id} appended",
    )

    return {
        "status": "review_journal_appended",
        "review_id": entry.review_id,
        "journal_path": str(resolved),
        "journal_entry": entry.to_json_dict(),
        "core_state_unchanged": True,
        "execution_enabled": False,
        "write_back_allowed": False,
    }


@mcp.tool()
async def get_review_journal_summary(
    journal_path: str = _REVIEW_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Return the append-only operator review journal summary."""
    payload, _resolved = _build_review_journal_summary_payload(journal_path=journal_path)
    return payload


@mcp.tool()
async def get_resolution_summary(
    journal_path: str = _REVIEW_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Return the latest per-source resolution summary derived from the review journal."""
    from app.research.operational_readiness import (
        build_review_journal_summary,
        build_review_resolution_summary,
        load_review_journal_entries,
    )

    _, resolved = _build_review_journal_summary_payload(journal_path=journal_path)

    entries = load_review_journal_entries(resolved)
    summary = build_review_journal_summary(entries, journal_path=resolved)
    return build_review_resolution_summary(summary).to_json_dict()


@mcp.tool()
async def get_alert_audit_summary(
    audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
) -> dict[str, object]:
    """Return a read-only summary of dispatched alert audit records.

    Reads from the alert audit JSONL trail and aggregates by channel.
    execution_enabled and write_back_allowed are always False.
    """
    from app.research.operational_readiness import _build_alert_dispatch_summary

    resolved = _resolve_workspace_dir(
        audit_dir,
        label="Alert audit directory",
    )
    audits = load_alert_audits(resolved)
    dispatch_summary = _build_alert_dispatch_summary(audits)
    return {
        "report_type": "alert_audit_summary",
        "execution_enabled": False,
        "write_back_allowed": False,
        **dispatch_summary.to_json_dict(),
    }


@mcp.tool()
async def get_decision_journal_summary(
    journal_path: str = _DECISION_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Return a read-only summary of the append-only decision journal.

    execution_enabled and write_back_allowed are always False.
    """
    from app.decisions.journal import (
        build_decision_journal_summary,
        load_decision_journal,
    )

    resolved = _resolve_workspace_path(
        journal_path,
        label="Decision journal",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    entries = load_decision_journal(resolved)
    summary = build_decision_journal_summary(entries, journal_path=resolved)
    return summary.to_json_dict()


@mcp.tool()
async def append_decision_instance(
    symbol: str,
    thesis: str,
    mode: str = "research",
    market: str = "crypto",
    venue: str = "paper",
    confidence_score: float = 0.5,
    supporting_factors: list[str] | None = None,
    contradictory_factors: list[str] | None = None,
    entry_logic: str = "manual_entry",
    exit_logic: str = "manual_exit",
    stop_loss: float = 0.0,
    invalidation_condition: str = "thesis_invalidated",
    model_version: str = "manual",
    prompt_version: str = "v0",
    data_sources_used: list[str] | None = None,
    journal_output_path: str = _DECISION_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Append a validated decision instance to the append-only decision journal.

    This is an audit-only write. execution_enabled and write_back_allowed remain False.
    No trade is triggered by this call.
    """
    from app.decisions.journal import (
        RiskAssessment,
        append_decision_jsonl,
        create_decision_instance,
    )

    resolved = _resolve_workspace_path(
        journal_output_path,
        label="Decision journal output",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    _require_artifacts_subpath(resolved, label="Decision journal output")

    risk = RiskAssessment(
        risk_level="unassessed",
        max_position_pct=0.0,
        drawdown_remaining_pct=100.0,
    )
    decision = create_decision_instance(
        symbol=symbol,
        market=market,
        venue=venue,
        mode=mode,
        thesis=thesis,
        supporting_factors=list(supporting_factors or ["mcp_operator_input"]),
        contradictory_factors=list(contradictory_factors or []),
        confidence_score=confidence_score,
        market_regime="unknown",
        volatility_state="unknown",
        liquidity_state="unknown",
        risk_assessment=risk,
        entry_logic=entry_logic,
        exit_logic=exit_logic,
        stop_loss=stop_loss,
        invalidation_condition=invalidation_condition,
        position_size_rationale="manual sizing",
        max_loss_estimate=0.0,
        data_sources_used=list(data_sources_used or ["operator_input"]),
        model_version=model_version,
        prompt_version=prompt_version,
    )
    append_decision_jsonl(decision, resolved)

    _append_mcp_write_audit(
        tool="append_decision_instance",
        params={
            "symbol": symbol,
            "mode": mode,
            "thesis": thesis[:80],
            "journal_output_path": str(resolved),
        },
        result_summary=f"decision_instance {decision.decision_id} appended",
    )

    return {
        "status": "decision_appended",
        "decision_id": decision.decision_id,
        "journal_path": str(resolved),
        "decision": decision.to_json_dict(),
        "execution_enabled": False,
        "write_back_allowed": False,
    }


@mcp.tool()
async def get_trading_loop_status(
    audit_path: str = _LOOP_AUDIT_DEFAULT_PATH,
    mode: str = "paper",
) -> dict[str, object]:
    """Return read-only trading-loop status and run-once guard state."""
    from app.orchestrator.trading_loop import build_loop_status_summary

    resolved = _resolve_workspace_path(
        audit_path,
        label="Loop audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    summary = build_loop_status_summary(audit_path=resolved, mode=mode)
    return summary.to_json_dict()


@mcp.tool()
async def get_recent_trading_cycles(
    audit_path: str = _LOOP_AUDIT_DEFAULT_PATH,
    last_n: int = 20,
) -> dict[str, object]:
    """Return read-only summary of recent trading-loop cycle audits."""
    from app.orchestrator.trading_loop import build_recent_cycles_summary

    resolved = _resolve_workspace_path(
        audit_path,
        label="Loop audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    summary = build_recent_cycles_summary(audit_path=resolved, last_n=last_n)
    return summary.to_json_dict()


@mcp.tool()
async def run_trading_loop_once(
    symbol: str = "BTC/USDT",
    mode: str = "paper",
    provider: str = "mock",
    analysis_profile: str = "conservative",
    loop_audit_path: str = _LOOP_AUDIT_DEFAULT_PATH,
    execution_audit_path: str = _PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Run one guarded paper/shadow cycle and append audit rows."""
    from app.orchestrator.trading_loop import run_trading_loop_once as run_once_cycle

    resolved_loop_audit = _resolve_workspace_path(
        loop_audit_path,
        label="Loop audit output",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    _require_artifacts_subpath(resolved_loop_audit, label="Loop audit output")

    resolved_execution_audit = _resolve_workspace_path(
        execution_audit_path,
        label="Execution audit output",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    _require_artifacts_subpath(resolved_execution_audit, label="Execution audit output")

    cycle = await run_once_cycle(
        symbol=symbol,
        mode=mode,
        provider=provider,
        analysis_profile=analysis_profile,
        loop_audit_path=resolved_loop_audit,
        execution_audit_path=resolved_execution_audit,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )

    cycle_payload = {
        "cycle_id": cycle.cycle_id,
        "started_at": cycle.started_at,
        "completed_at": cycle.completed_at,
        "symbol": cycle.symbol,
        "status": cycle.status.value,
        "market_data_fetched": cycle.market_data_fetched,
        "signal_generated": cycle.signal_generated,
        "risk_approved": cycle.risk_approved,
        "order_created": cycle.order_created,
        "fill_simulated": cycle.fill_simulated,
        "decision_id": cycle.decision_id,
        "risk_check_id": cycle.risk_check_id,
        "order_id": cycle.order_id,
        "notes": list(cycle.notes),
    }

    _append_mcp_write_audit(
        tool="run_trading_loop_once",
        params={
            "symbol": symbol,
            "mode": mode,
            "provider": provider,
            "analysis_profile": analysis_profile,
            "loop_audit_path": str(resolved_loop_audit),
            "execution_audit_path": str(resolved_execution_audit),
        },
        result_summary=(
            f"trading_loop cycle {cycle.cycle_id} completed with status={cycle.status.value}"
        ),
    )

    return {
        "status": "cycle_completed",
        "mode": mode,
        "provider": provider,
        "analysis_profile": analysis_profile,
        "loop_audit_path": str(resolved_loop_audit),
        "execution_audit_path": str(resolved_execution_audit),
        "cycle": cycle_payload,
        "auto_loop_enabled": False,
        "execution_enabled": False,
        "write_back_allowed": False,
    }


@mcp.tool()
async def get_loop_cycle_summary(
    audit_path: str = _LOOP_AUDIT_DEFAULT_PATH,
    last_n: int = 20,
) -> dict[str, object]:
    """Compatibility alias for get_recent_trading_cycles."""
    return await get_recent_trading_cycles(audit_path=audit_path, last_n=last_n)  # type: ignore[no-any-return]


if __name__ == "__main__":
    mcp.run(transport="stdio")
