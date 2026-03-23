"""Canonical read-only MCP tool inventory.

This module defines the authoritative list of read-only MCP tools.
These tools never mutate operator state and have no write-back side effects.

The tool implementations are registered in app.agents.mcp_server via @mcp.tool().
This module exports the inventory for contract tests, documentation, and
future inlining of tool logic during the incremental MCP-split refactor.

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

# ---------------------------------------------------------------------------
# Canonical inventory (mirrors _CANONICAL_MCP_READ_TOOL_NAMES in mcp_server)
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
