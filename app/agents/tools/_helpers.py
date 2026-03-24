"""Internal helper functions shared by MCP tool modules.

This module provides path-resolution, write-audit, and report-building
helpers used by canonical_read and guarded_write tool implementations.

Design rules:
- Never import from app.agents.mcp_server (circular-import guard).
- No FastMCP imports — helpers are framework-agnostic.
- All path helpers enforce workspace / artifacts/ invariants (I-94, I-95).
"""

from __future__ import annotations

import datetime
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.alerts.audit import load_alert_audits
from app.core.domain.document import CanonicalDocument
from app.core.settings import get_settings
from app.research.abc_result import load_abc_inference_envelopes
from app.research.active_route import (
    DEFAULT_ACTIVE_ROUTE_PATH,
    load_active_route_state,
)
from app.research.distribution import (
    build_handoff_collector_summary,
)
from app.research.execution_handoff import (
    HANDOFF_ACK_JSONL_FILENAME,
    load_handoff_acknowledgements,
    load_signal_handoffs,
)
from app.research.operational_readiness import (
    ArtifactRef,
    OperationalArtifactRefs,
    OperationalReadinessReport,
    build_operational_readiness_report,
)
from app.research.signals import SignalCandidate, extract_signal_candidates
from app.research.watchlists import WatchlistRegistry
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workspace constants
# ---------------------------------------------------------------------------

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_SUBDIR = "artifacts"
JSON_SUFFIXES = frozenset({".json"})
ARTIFACT_SUFFIXES = frozenset({".json", ".jsonl"})
HANDOFF_ACK_DEFAULT_PATH = f"artifacts/{HANDOFF_ACK_JSONL_FILENAME}"
ALERT_AUDIT_DEFAULT_DIR = ARTIFACTS_SUBDIR
REVIEW_JOURNAL_DEFAULT_PATH = "artifacts/operator_review_journal.jsonl"
PAPER_EXECUTION_AUDIT_DEFAULT_PATH = "artifacts/paper_execution_audit.jsonl"
DECISION_JOURNAL_DEFAULT_PATH = "artifacts/decision_journal.jsonl"
LOOP_AUDIT_DEFAULT_PATH = "artifacts/trading_loop_audit.jsonl"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def resolve_workspace_path(
    path_value: str | Path,
    *,
    label: str,
    must_exist: bool = False,
    allowed_suffixes: frozenset[str] = ARTIFACT_SUFFIXES,
) -> Path:
    candidate = Path(path_value)
    resolved = (candidate if candidate.is_absolute() else WORKSPACE_ROOT / candidate).resolve()
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as err:
        raise ValueError(f"{label} must stay within workspace: {path_value}") from err

    if resolved.suffix.lower() not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValueError(f"{label} must use one of: {allowed}")

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{label} not found: {resolved}")

    return resolved


def require_artifacts_subpath(resolved: Path, *, label: str) -> Path:
    """Ensure resolved path is inside workspace/artifacts/ (I-95: write guard)."""
    artifacts_root = WORKSPACE_ROOT / ARTIFACTS_SUBDIR
    try:
        resolved.relative_to(artifacts_root)
    except ValueError as err:
        raise ValueError(f"{label} must be within workspace/artifacts/: {resolved}") from err
    return resolved


def resolve_workspace_dir(
    path_value: str | Path,
    *,
    label: str,
    must_exist: bool = False,
) -> Path:
    candidate = Path(path_value)
    resolved = (candidate if candidate.is_absolute() else WORKSPACE_ROOT / candidate).resolve()
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as err:
        raise ValueError(f"{label} must stay within workspace: {path_value}") from err

    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"{label} must be a directory: {resolved}")

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{label} not found: {resolved}")

    return resolved


# ---------------------------------------------------------------------------
# Write audit (I-94)
# ---------------------------------------------------------------------------


def append_mcp_write_audit(
    *,
    tool: str,
    params: dict[str, object],
    result_summary: str,
) -> None:
    """Append a write audit entry to artifacts/mcp_write_audit.jsonl (I-94).

    Never raises — a failing audit must not suppress the original result.
    """
    audit_path = WORKSPACE_ROOT / ARTIFACTS_SUBDIR / "mcp_write_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.datetime.now(tz=datetime.UTC).isoformat(),
        "tool": tool,
        "params": params,
        "result_summary": result_summary,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Handoff collector
# ---------------------------------------------------------------------------


def build_handoff_collector_report(
    *,
    handoff_path: str | Path,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
) -> tuple[dict[str, object], Path, Path]:
    resolved_handoff = resolve_workspace_path(
        handoff_path,
        label="Signal handoff input",
        must_exist=True,
    )
    resolved_ack = resolve_workspace_path(
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


# ---------------------------------------------------------------------------
# Operational readiness
# ---------------------------------------------------------------------------


def build_operational_readiness_report_helper(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> OperationalReadinessReport:
    resolved_handoff: Path | None = None
    handoffs = []
    if handoff_path is not None:
        resolved_handoff = resolve_workspace_path(
            handoff_path,
            label="Signal handoff input",
        )
        if resolved_handoff.exists():
            handoffs = load_signal_handoffs(resolved_handoff)

    resolved_ack = resolve_workspace_path(
        acknowledgement_path,
        label="Handoff acknowledgement audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    acknowledgements = load_handoff_acknowledgements(resolved_ack)
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = resolve_workspace_path(
        state_path,
        label="Active route state",
        allowed_suffixes=JSON_SUFFIXES,
    )
    active_route_state = load_active_route_state(resolved_state)

    resolved_abc: Path | None = None
    effective_abc_path = abc_output_path
    if effective_abc_path is None and active_route_state is not None:
        effective_abc_path = active_route_state.abc_envelope_output

    envelopes = []
    if effective_abc_path is not None:
        resolved_abc = resolve_workspace_path(
            effective_abc_path,
            label="ABC envelope output",
            allowed_suffixes=frozenset({".json", ".jsonl"}),
        )
        if resolved_abc.exists():
            envelopes = load_abc_inference_envelopes(resolved_abc)

    resolved_alert_dir = resolve_workspace_dir(
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


def build_operational_readiness_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    return build_operational_readiness_report_helper(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    ).to_json_dict()


def filter_readiness_issues(
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


def build_provider_health_payload(
    *,
    handoff_path: str | Path | None = None,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
) -> dict[str, object]:
    readiness_payload = build_operational_readiness_payload(
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
        "issues": filter_readiness_issues(
            readiness_payload,
            category="provider_health",
        ),
    }


def build_distribution_drift_payload(
    *,
    handoff_path: str | Path | None = None,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
) -> dict[str, object]:
    readiness_payload = build_operational_readiness_payload(
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
        "issues": filter_readiness_issues(
            readiness_payload,
            category="distribution_drift",
        ),
    }


def build_protective_gate_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    readiness_payload = build_operational_readiness_payload(
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


def build_remediation_recommendation_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    gate_payload = build_protective_gate_payload(
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


def build_operational_escalation_report(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> Any:
    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )
    from app.research.operational_readiness import build_operational_escalation_summary

    readiness_report = build_operational_readiness_report_helper(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )
    resolved_dir = resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = resolve_workspace_path(state_path, label="state_path")
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


def build_escalation_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, Any]:
    summary = build_operational_escalation_report(
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


def build_blocking_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import build_blocking_summary

    escalation_summary = build_operational_escalation_report(
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


def build_operator_action_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import build_operator_action_summary

    escalation_summary = build_operational_escalation_report(
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


def build_action_queue_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import build_action_queue_summary

    escalation_summary = build_operational_escalation_report(
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


def build_blocking_actions_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_blocking_actions,
    )

    escalation_summary = build_operational_escalation_report(
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


def build_prioritized_actions_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_prioritized_actions,
    )

    escalation_summary = build_operational_escalation_report(
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


def build_review_required_actions_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_review_required_actions,
    )

    escalation_summary = build_operational_escalation_report(
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


# ---------------------------------------------------------------------------
# Signal candidates loader (shared by several read tools)
# ---------------------------------------------------------------------------


async def load_signal_candidates_and_documents(
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


# ---------------------------------------------------------------------------
# Paper portfolio helper
# ---------------------------------------------------------------------------


async def build_paper_portfolio_snapshot_helper(
    *,
    audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> Any:
    from app.execution.portfolio_read import build_portfolio_snapshot

    resolved = resolve_workspace_path(
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


# ---------------------------------------------------------------------------
# Operator decision pack helpers
# ---------------------------------------------------------------------------


def build_operator_decision_pack_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
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

    readiness_report = build_operational_readiness_report_helper(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )
    resolved_dir = resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = resolve_workspace_path(state_path, label="state_path")
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


def build_operator_runbook_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    from app.cli.research import (
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

    readiness_report = build_operational_readiness_report_helper(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )
    resolved_dir = resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = resolve_workspace_path(state_path, label="state_path")
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
    invalid_refs = get_invalid_research_command_refs(extract_runbook_command_refs(payload))
    if invalid_refs:
        raise ValueError(
            "Operator runbook contains invalid research command references: "
            + ", ".join(invalid_refs)
        )
    return payload


# ---------------------------------------------------------------------------
# Review journal helper
# ---------------------------------------------------------------------------


def build_review_journal_summary_payload(
    *,
    journal_path: str | Path = REVIEW_JOURNAL_DEFAULT_PATH,
) -> tuple[dict[str, object], Path]:
    from app.research.operational_readiness import (
        build_review_journal_summary,
        load_review_journal_entries,
    )

    resolved = resolve_workspace_path(
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


# ---------------------------------------------------------------------------
# Daily operator summary helper
# ---------------------------------------------------------------------------


async def safe_daily_surface_load(
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
