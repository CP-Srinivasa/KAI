"""Canonical read-only MCP tool implementations.

This module defines both the authoritative tool-name inventory and the
actual async tool functions for all read-only MCP tools.

Tools are registered in app.agents.mcp_server via mcp.add_tool().
No @mcp.tool() decorator is used here -- this module is framework-agnostic.

Design invariants:
- All functions are pure reads: no filesystem writes, no DB mutations.
- No imports from app.agents.mcp_server (circular-import guard).
- execution_enabled is always False.
- write_back_allowed is always False.
- Companion-ML subsystem removed: affected tools return stubs.

Tool categories:
- watchlist / research: get_watchlists, get_research_brief, get_signal_candidates
- market data: get_market_data_quote
- portfolio: get_paper_portfolio_snapshot, get_paper_positions_summary, get_paper_exposure_summary
- narrative: get_narrative_clusters, get_signals_for_execution
- distribution / route: get_distribution_classification_report (stub),
  get_route_profile_report (stub), get_inference_route_profile (stub),
  get_active_route_status (stub), get_upgrade_cycle_status (stub)
- handoff: get_handoff_collector_summary (stub)
- readiness: get_operational_readiness_summary (stub), get_provider_health (stub),
  get_distribution_drift (stub), get_protective_gate_summary (stub),
  get_remediation_recommendations (stub)
- artifact lifecycle: get_artifact_inventory (stub), get_artifact_retention_report (stub),
  get_cleanup_eligibility_summary (stub), get_protected_artifact_summary (stub),
  get_review_required_summary (stub)
- escalation / actions: get_escalation_summary (stub), get_blocking_summary (stub),
  get_operator_action_summary (stub), get_action_queue_summary (stub), get_blocking_actions (stub),
  get_prioritized_actions (stub), get_review_required_actions (stub)
- decision pack / daily: get_decision_pack_summary (stub), get_daily_operator_summary (stub),
  get_operator_runbook (stub), get_review_journal_summary (stub), get_resolution_summary (stub)
- alerts / journal: get_alert_audit_summary, get_decision_journal_summary
- trading loop: get_trading_loop_status, get_recent_trading_cycles
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agents.tools._helpers import (
    _COMPANION_ML_STUB,
    ALERT_AUDIT_DEFAULT_DIR,
    ARTIFACTS_SUBDIR,
    DECISION_JOURNAL_DEFAULT_PATH,
    HANDOFF_ACK_DEFAULT_PATH,
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
)
from app.core.briefs import ResearchBriefBuilder
from app.core.settings import get_settings
from app.core.signals import extract_signal_candidates
from app.core.watchlists import WatchlistRegistry, parse_watchlist_type
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

# ---------------------------------------------------------------------------
# Canonical inventory (authoritative list)
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

    Pure read-only projection -- no DB writes, no routing changes (I-184).
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
    candidates, _docs = await load_signal_candidates_and_documents(
        watchlist=watchlist,
        min_priority=min_priority,
        limit=limit,
        provider=provider,
    )
    return {
        "report_type": "execution_handoff_report",
        "execution_enabled": False,
        "write_back_allowed": False,
        "candidate_count": len(candidates),
        "candidates": [c.to_json_dict() for c in candidates],
    }


async def get_distribution_classification_report(
    abc_output_path: str,
    watchlist: str | None = None,
    min_priority: int = 8,
    limit: int = 50,
    provider: str | None = None,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {
        **_COMPANION_ML_STUB,
        "report_type": "distribution_classification_report",
        "abc_output_path": abc_output_path,
    }


async def get_route_profile_report(limit: int = 1000) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "route_profile_report"}


async def get_inference_route_profile(profile_path: str) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {
        **_COMPANION_ML_STUB,
        "report_type": "inference_route_profile",
        "profile_path": profile_path,
    }


async def get_active_route_status(
    state_path: str = "artifacts/active_route_state.json",
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {
        **_COMPANION_ML_STUB,
        "report_type": "active_route_status",
        "active": False,
        "state_path": state_path,
    }


async def get_upgrade_cycle_status(
    teacher_dataset_path: str,
    training_job_record_path: str | None = None,
    evaluation_report_path: str | None = None,
    comparison_report_path: str | None = None,
    promotion_record_path: str | None = None,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "upgrade_cycle_status"}


async def get_handoff_collector_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
) -> dict[str, object]:
    """Summarize pending and acknowledged handoffs from existing audit artifacts only.

    When *handoff_path* is omitted or None the function returns an empty collector
    summary (no handoff file configured) without raising.
    """
    if handoff_path is None:
        return {
            "report_type": "handoff_collector_summary",
            "handoff_path": None,
            "acknowledgement_path": acknowledgement_path,
            "total_handoffs": 0,
            "pending": 0,
            "acknowledged": 0,
            "handoffs": [],
            "status": "no_handoff_path_configured",
        }
    payload, _resolved_handoff, _resolved_ack = build_handoff_collector_report(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
    )
    return payload


async def get_operational_readiness_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return build_provider_health_payload(
        handoff_path=handoff_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
    )


async def get_distribution_drift(
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return build_distribution_drift_payload(
        handoff_path=handoff_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
    )


async def get_protective_gate_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    """Stub: companion-ML subsystem removed."""
    return {
        **_COMPANION_ML_STUB,
        "report_type": "artifact_inventory",
        "artifacts_dir": artifacts_dir,
    }


async def get_artifact_retention_report(
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = "artifacts/active_route_state.json",
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "artifact_retention_report"}


async def get_cleanup_eligibility_summary(
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = "artifacts/active_route_state.json",
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "cleanup_eligibility_summary"}


async def get_protected_artifact_summary(
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = "artifacts/active_route_state.json",
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "protected_artifact_summary"}


async def get_review_required_summary(
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    stale_after_days: float = 30.0,
    state_path: str = "artifacts/active_route_state.json",
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "review_required_summary"}


async def get_escalation_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    state_path: str = "artifacts/active_route_state.json",
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
    """Stub: companion-ML subsystem removed (daily summary aggregate)."""
    return {
        **_COMPANION_ML_STUB,
        "report_type": "daily_operator_summary",
    }


async def get_operator_runbook(
    handoff_path: str | None = None,
    acknowledgement_path: str = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = "artifacts/active_route_state.json",
    abc_output_path: str | None = None,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
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
    """Stub: companion-ML subsystem removed."""
    payload, _resolved = build_review_journal_summary_payload(journal_path=journal_path)
    return payload


async def get_resolution_summary(
    journal_path: str = REVIEW_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "resolution_summary"}


async def get_alert_audit_summary(
    audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
) -> dict[str, object]:
    """Return a read-only summary of dispatched alert audit records.

    Reads from the alert audit JSONL trail and aggregates by channel.
    execution_enabled and write_back_allowed are always False.
    """
    from app.alerts.audit import load_alert_audits

    resolved = resolve_workspace_dir(
        audit_dir,
        label="Alert audit directory",
    )
    audits = load_alert_audits(resolved)
    return {
        "report_type": "alert_audit_summary",
        "execution_enabled": False,
        "write_back_allowed": False,
        "total_alerts": len(audits),
        "alerts": [a.to_json_dict() for a in audits] if audits else [],
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
