"""Canonical read-only MCP tool implementations.

This module defines both the authoritative tool-name inventory and the
actual async tool functions for all read-only MCP tools.

Tools are registered in app.agents.mcp_server via mcp.add_tool().
No @mcp.tool() decorator is used here — this module is framework-agnostic.

Design invariants:
- All functions are pure reads: no filesystem writes, no DB mutations.
- No imports from app.agents.mcp_server (circular-import guard).
- execution_enabled is always False.
- write_back_allowed is always False.

Tool categories:
- watchlist / research: get_watchlists, get_research_brief, get_signal_candidates
- market data: get_market_data_quote
- portfolio: get_paper_portfolio_snapshot, get_paper_positions_summary, get_paper_exposure_summary
- narrative: get_narrative_clusters, get_signals_for_execution
- distribution / route: get_distribution_classification_report, get_route_profile_report,
  get_inference_route_profile, get_active_route_status, get_upgrade_cycle_status
- handoff: get_handoff_collector_summary
- readiness: get_operational_readiness_summary, get_provider_health,
  get_distribution_drift, get_protective_gate_summary, get_remediation_recommendations
- artifact lifecycle: get_artifact_inventory, get_artifact_retention_report,
  get_cleanup_eligibility_summary, get_protected_artifact_summary, get_review_required_summary
- escalation / actions: get_escalation_summary, get_blocking_summary,
  get_operator_action_summary, get_action_queue_summary, get_blocking_actions,
  get_prioritized_actions, get_review_required_actions
- decision pack / daily: get_decision_pack_summary, get_daily_operator_summary,
  get_operator_runbook, get_review_journal_summary, get_resolution_summary
- alerts / journal: get_alert_audit_summary, get_decision_journal_summary
- trading loop: get_trading_loop_status, get_recent_trading_cycles
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agents.tools._helpers import (
    ALERT_AUDIT_DEFAULT_DIR,
    ARTIFACTS_SUBDIR,
    DECISION_JOURNAL_DEFAULT_PATH,
    HANDOFF_ACK_DEFAULT_PATH,
    JSON_SUFFIXES,
    LOOP_AUDIT_DEFAULT_PATH,
    PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    REVIEW_JOURNAL_DEFAULT_PATH,
    build_action_queue_summary_payload,
    build_blocking_actions_payload,
    build_blocking_summary_payload,
    build_distribution_drift_payload,
    build_escalation_summary_payload,
    build_handoff_collector_report,
    build_operational_readiness_payload,
    build_operator_action_summary_payload,
    build_operator_decision_pack_payload,
    build_operator_runbook_payload,
    build_paper_portfolio_snapshot_helper,
    build_prioritized_actions_payload,
    build_protective_gate_payload,
    build_provider_health_payload,
    build_remediation_recommendation_payload,
    build_review_journal_summary_payload,
    build_review_required_actions_payload,
    load_signal_candidates_and_documents,
    resolve_workspace_dir,
    resolve_workspace_path,
    safe_daily_surface_load,
)
from app.core.settings import get_settings
from app.research.abc_result import load_abc_inference_envelopes
from app.research.active_route import DEFAULT_ACTIVE_ROUTE_PATH, load_active_route_state
from app.research.briefs import ResearchBriefBuilder
from app.research.distribution import (
    build_distribution_classification_report,
    build_execution_handoff_report,
    build_route_profile,
)
from app.research.inference_profile import load_inference_route_profile
from app.research.signals import extract_signal_candidates
from app.research.upgrade_cycle import build_upgrade_cycle_report
from app.research.watchlists import WatchlistRegistry, parse_watchlist_type
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

# ---------------------------------------------------------------------------
# Canonical inventory (authoritative list — mirrors _CANONICAL_MCP_READ_TOOL_NAMES
# in mcp_server and is validated by contract tests)
# ---------------------------------------------------------------------------

CANONICAL_READ_TOOL_NAMES: tuple[str, ...] = (
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


def get_canonical_read_tool_names() -> tuple[str, ...]:
    """Return the locked canonical read-only tool name tuple."""
    return CANONICAL_READ_TOOL_NAMES


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def get_watchlists(watchlist_type: str = "assets") -> dict[str, list[str]]:
    """List available research watchlists or show the members of watchlists."""
    settings = get_settings()
    registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))
    resolved_type = parse_watchlist_type(watchlist_type)
    all_watchlists = registry.get_all_watchlists(item_type=resolved_type)
    return dict(all_watchlists)


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


async def get_signal_candidates(
    watchlist: str | None = None, min_priority: int = 8, limit: int = 50
) -> str:
    """Generate actionable signal candidates from analyzed documents."""
    candidates, _docs = await load_signal_candidates_and_documents(
        watchlist=watchlist,
        min_priority=min_priority,
        limit=limit,
    )
    return json.dumps([c.to_json_dict() for c in candidates], indent=2)


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


async def get_paper_portfolio_snapshot(
    audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Return canonical read-only paper portfolio snapshot from audit replay."""
    snapshot = await build_paper_portfolio_snapshot_helper(
        audit_path=audit_path,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return snapshot.to_json_dict()  # type: ignore[no-any-return]


async def get_paper_positions_summary(
    audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Return positions-only slice from canonical paper portfolio snapshot."""
    from app.execution.portfolio_read import build_positions_summary

    snapshot = await build_paper_portfolio_snapshot_helper(
        audit_path=audit_path,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return build_positions_summary(snapshot)


async def get_paper_exposure_summary(
    audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Return exposure-only slice from canonical paper portfolio snapshot."""
    from app.execution.portfolio_read import build_exposure_summary

    snapshot = await build_paper_portfolio_snapshot_helper(
        audit_path=audit_path,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return build_exposure_summary(snapshot)


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


async def get_signals_for_execution(
    watchlist: str | None = None,
    min_priority: int = 8,
    limit: int = 50,
    provider: str | None = None,
) -> dict[str, object]:
    """Return a read-only external-consumption handoff for qualified signals."""
    candidates, docs = await load_signal_candidates_and_documents(
        watchlist=watchlist,
        min_priority=min_priority,
        limit=limit,
        provider=provider,
    )
    report = build_execution_handoff_report(candidates, docs)
    return report.to_json_dict()


async def get_distribution_classification_report(
    abc_output_path: str,
    watchlist: str | None = None,
    min_priority: int = 8,
    limit: int = 50,
    provider: str | None = None,
) -> dict[str, object]:
    """Read a route-aware distribution report from existing ABC audit envelopes only."""
    resolved = resolve_workspace_path(
        abc_output_path,
        label="ABC envelope output",
        must_exist=True,
    )
    envelopes = load_abc_inference_envelopes(resolved)
    candidates, docs = await load_signal_candidates_and_documents(
        watchlist=watchlist,
        min_priority=min_priority,
        limit=limit,
        provider=provider,
    )
    report = build_distribution_classification_report(candidates, docs, envelopes)
    payload = report.to_json_dict()
    payload["abc_output_path"] = str(resolved)
    return payload


async def get_route_profile_report(limit: int = 1000) -> dict[str, object]:
    """Build the current route/distribution report from stored analyzed documents."""
    settings = get_settings()
    session_factory = build_session_factory(settings.db)
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        report = await build_route_profile(repo, limit=limit)
    return report.to_json_dict()


async def get_inference_route_profile(profile_path: str) -> dict[str, object]:
    """Load a saved inference route profile from a workspace-local JSON file."""
    resolved = resolve_workspace_path(
        profile_path,
        label="Inference route profile",
        must_exist=True,
        allowed_suffixes=JSON_SUFFIXES,
    )
    profile = load_inference_route_profile(resolved)
    payload = profile.to_json_dict()
    payload["path"] = str(resolved)
    return payload


async def get_active_route_status(
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Read the current active route state without changing routing or providers."""
    resolved = resolve_workspace_path(
        state_path,
        label="Active route state",
        allowed_suffixes=JSON_SUFFIXES,
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


async def get_upgrade_cycle_status(
    teacher_dataset_path: str,
    training_job_record_path: str | None = None,
    evaluation_report_path: str | None = None,
    comparison_report_path: str | None = None,
    promotion_record_path: str | None = None,
) -> dict[str, object]:
    """Summarize upgrade-cycle status from existing workspace-local artifacts only."""
    teacher_path = resolve_workspace_path(
        teacher_dataset_path,
        label="Teacher dataset",
        must_exist=True,
    )
    training_path = (
        resolve_workspace_path(
            training_job_record_path,
            label="Training job record",
            must_exist=True,
        )
        if training_job_record_path is not None
        else None
    )
    evaluation_path = (
        resolve_workspace_path(
            evaluation_report_path,
            label="Evaluation report",
            must_exist=True,
        )
        if evaluation_report_path is not None
        else None
    )
    comparison_path = (
        resolve_workspace_path(
            comparison_report_path,
            label="Comparison report",
            must_exist=True,
        )
        if comparison_report_path is not None
        else None
    )
    promotion_path = (
        resolve_workspace_path(
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


async def get_handoff_collector_summary(
    handoff_path: str,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
) -> dict[str, object]:
    """Summarize pending and acknowledged handoffs from existing audit artifacts only."""
    payload, _resolved_handoff, _resolved_ack = build_handoff_collector_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
    )
    return payload


async def get_operational_readiness_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Build a read-only operational readiness summary from existing artifacts only."""
    return build_operational_readiness_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )


async def get_provider_health(
    handoff_path: str | None = None,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
) -> dict[str, object]:
    """Return the readiness-derived provider health slice only.

    This is a bounded read view over the canonical operational readiness stack,
    not a second monitoring implementation.
    """
    return build_provider_health_payload(
        handoff_path=handoff_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
    )


async def get_distribution_drift(
    handoff_path: str | None = None,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
) -> dict[str, object]:
    """Return the readiness-derived distribution drift slice only."""
    return build_distribution_drift_payload(
        handoff_path=handoff_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
    )


async def get_protective_gate_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Return the readiness-derived protective gate view only."""
    return build_protective_gate_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )


async def get_remediation_recommendations(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Return read-only remediation hints derived from protective gate items."""
    return build_remediation_recommendation_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )


async def get_artifact_inventory(
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return a read-only inventory of managed artifact files (I-149).

    Scans the artifacts directory and reports file age, size, and stale status.
    execution_enabled is always False (I-150). No filesystem writes.
    """
    from app.research.artifact_lifecycle import build_artifact_inventory

    resolved_dir = resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    report = build_artifact_inventory(resolved_dir, stale_after_days=stale_after_days)
    return report.to_json_dict()


async def get_artifact_retention_report(
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Return read-only artifact retention classification (I-153-I-161, Sprint 25).

    Classifies each artifact as protected, rotatable, or review_required.
    No filesystem mutations. execution_enabled and write_back_allowed are always False.
    delete_eligible_count is always 0 - deletion is never platform-initiated (I-154).
    """
    from app.research.artifact_lifecycle import build_retention_report

    resolved_dir = resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = resolve_workspace_path(state_path, label="state_path")
    active_route_active = resolved_state.exists()

    report = build_retention_report(
        resolved_dir,
        stale_after_days=stale_after_days,
        active_route_active=active_route_active,
    )
    return report.to_json_dict()


async def get_cleanup_eligibility_summary(
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Return cleanup/archive eligibility derived from the canonical retention report."""
    from app.research.artifact_lifecycle import (
        build_cleanup_eligibility_summary,
        build_retention_report,
    )

    resolved_dir = resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = resolve_workspace_path(state_path, label="state_path")
    report = build_retention_report(
        resolved_dir,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    return build_cleanup_eligibility_summary(report).to_json_dict()


async def get_protected_artifact_summary(
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Return the protected-artifact slice derived from the canonical retention report."""
    from app.research.artifact_lifecycle import (
        build_protected_artifact_summary,
        build_retention_report,
    )

    resolved_dir = resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = resolve_workspace_path(state_path, label="state_path")
    report = build_retention_report(
        resolved_dir,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    return build_protected_artifact_summary(report).to_json_dict()


async def get_review_required_summary(
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Return the review-required slice derived from the canonical retention report (Sprint 26)."""
    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )

    resolved_dir = resolve_workspace_dir(artifacts_dir, label="artifacts_dir")
    resolved_state = resolve_workspace_path(state_path, label="state_path")
    report = build_retention_report(
        resolved_dir,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    return build_review_required_summary(report).to_json_dict()


async def get_escalation_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the canonical safe operational escalation summary."""
    return build_escalation_summary_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def get_blocking_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the blocking-only slice of the canonical escalation surface."""
    return build_blocking_summary_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def get_operator_action_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the operator-action-required slice of the canonical escalation surface."""
    return build_operator_action_summary_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def get_action_queue_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the canonical safe operator action queue derived from escalation only."""
    return build_action_queue_summary_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def get_blocking_actions(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the blocking-only slice of the canonical operator action queue."""
    return build_blocking_actions_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def get_prioritized_actions(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the operator action queue in derived priority order only."""
    return build_prioritized_actions_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def get_review_required_actions(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return review-required items from the canonical operator action queue only."""
    return build_review_required_actions_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def get_decision_pack_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the canonical read-only operator decision pack summary.

    Bundles readiness status, escalation, action queue, and governance snapshots
    into a single situation-awareness surface. Advisory only - no execution
    authority. Decision pack is a derived snapshot; sub-report surfaces remain
    the source of truth. I-185-I-192.
    """
    return build_operator_decision_pack_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def get_daily_operator_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
    loop_audit_path: str = LOOP_AUDIT_DEFAULT_PATH,
    loop_last_n: int = 50,
    portfolio_audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    market_data_provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    review_journal_path: str = REVIEW_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Return one canonical daily operator aggregate from existing read surfaces only."""
    from app.research.operational_readiness import build_daily_operator_summary

    readiness_summary = await safe_daily_surface_load(
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
    recent_cycles_summary = await safe_daily_surface_load(
        source_name="recent_cycles",
        loader=lambda: get_recent_trading_cycles(
            audit_path=loop_audit_path,
            last_n=loop_last_n,
        ),
    )
    portfolio_snapshot = await safe_daily_surface_load(
        source_name="portfolio_snapshot",
        loader=lambda: get_paper_portfolio_snapshot(
            audit_path=portfolio_audit_path,
            provider=market_data_provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        ),
    )
    exposure_summary = await safe_daily_surface_load(
        source_name="exposure_summary",
        loader=lambda: get_paper_exposure_summary(
            audit_path=portfolio_audit_path,
            provider=market_data_provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        ),
    )
    decision_pack_summary = await safe_daily_surface_load(
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
    review_journal_summary = await safe_daily_surface_load(
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


async def get_operator_runbook(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Return the canonical read-only operator runbook with validated commands."""
    return build_operator_runbook_payload(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
        artifacts_dir=artifacts_dir,
        retention_stale_after_days=retention_stale_after_days,
    )


async def get_review_journal_summary(
    journal_path: str = REVIEW_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Return the append-only operator review journal summary."""
    payload, _resolved = build_review_journal_summary_payload(journal_path=journal_path)
    return payload


async def get_resolution_summary(
    journal_path: str = REVIEW_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Return the latest per-source resolution summary derived from the review journal."""
    from app.research.operational_readiness import (
        build_review_journal_summary,
        build_review_resolution_summary,
        load_review_journal_entries,
    )

    _, resolved = build_review_journal_summary_payload(journal_path=journal_path)

    entries = load_review_journal_entries(resolved)
    summary = build_review_journal_summary(entries, journal_path=resolved)
    return build_review_resolution_summary(summary).to_json_dict()


async def get_alert_audit_summary(
    audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
) -> dict[str, object]:
    """Return a read-only summary of dispatched alert audit records.

    Reads from the alert audit JSONL trail and aggregates by channel.
    execution_enabled and write_back_allowed are always False.
    """
    from app.alerts.audit import load_alert_audits
    from app.research.operational_readiness import _build_alert_dispatch_summary

    resolved = resolve_workspace_dir(
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


async def get_decision_journal_summary(
    journal_path: str = DECISION_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Return a read-only summary of the append-only decision journal.

    execution_enabled and write_back_allowed are always False.
    """
    from app.decisions.journal import (
        build_decision_journal_summary,
        load_decision_journal,
    )

    resolved = resolve_workspace_path(
        journal_path,
        label="Decision journal",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    entries = load_decision_journal(resolved)
    summary = build_decision_journal_summary(entries, journal_path=resolved)
    return summary.to_json_dict()


async def get_trading_loop_status(
    audit_path: str = LOOP_AUDIT_DEFAULT_PATH,
    mode: str = "paper",
) -> dict[str, object]:
    """Return read-only trading-loop status and run-once guard state."""
    from app.orchestrator.trading_loop import build_loop_status_summary

    resolved = resolve_workspace_path(
        audit_path,
        label="Loop audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    summary = build_loop_status_summary(audit_path=resolved, mode=mode)
    return summary.to_json_dict()


async def get_recent_trading_cycles(
    audit_path: str = LOOP_AUDIT_DEFAULT_PATH,
    last_n: int = 20,
) -> dict[str, object]:
    """Return read-only summary of recent trading-loop cycle audits."""
    from app.orchestrator.trading_loop import build_recent_cycles_summary

    resolved = resolve_workspace_path(
        audit_path,
        label="Loop audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    summary = build_recent_cycles_summary(audit_path=resolved, last_n=last_n)
    return summary.to_json_dict()
