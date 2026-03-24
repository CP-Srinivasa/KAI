"""KAI MCP Server - server setup, tool registration, and public re-exports.

This module is intentionally thin:
- Initialises the FastMCP server instance.
- Imports tool functions from app.agents.tools sub-modules.
- Registers every tool via mcp.add_tool() so the MCP surface is intact.
- Re-exports all tool functions and inventory helpers so existing test
  imports (from app.agents.mcp_server import ...) continue to work without
  modification.

Tool implementations live in:
  app/agents/tools/canonical_read.py  - read-only tools
  app/agents/tools/guarded_write.py   - guarded write tools
  app/agents/tools/compat.py          - aliases, superseded tools, workflow helpers
  app/agents/tools/_helpers.py        - shared path/report helpers
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from app.agents.tools._helpers import (
    WORKSPACE_ROOT as _WORKSPACE_ROOT,  # noqa: F401 - re-exported for test monkeypatching
)
from app.agents.tools.canonical_read import (
    get_action_queue_summary,
    get_active_route_status,
    get_alert_audit_summary,
    get_artifact_inventory,
    get_artifact_retention_report,
    get_blocking_actions,
    get_blocking_summary,
    get_cleanup_eligibility_summary,
    get_daily_operator_summary,
    get_decision_journal_summary,
    get_decision_pack_summary,
    get_distribution_classification_report,
    get_distribution_drift,
    get_escalation_summary,
    get_handoff_collector_summary,
    get_inference_route_profile,
    get_market_data_quote,
    get_narrative_clusters,
    get_operational_readiness_summary,
    get_operator_action_summary,
    get_operator_runbook,
    get_paper_exposure_summary,
    get_paper_portfolio_snapshot,
    get_paper_positions_summary,
    get_prioritized_actions,
    get_protected_artifact_summary,
    get_protective_gate_summary,
    get_provider_health,
    get_recent_trading_cycles,
    get_remediation_recommendations,
    get_research_brief,
    get_resolution_summary,
    get_review_journal_summary,
    get_review_required_actions,
    get_review_required_summary,
    get_route_profile_report,
    get_signal_candidates,
    get_signals_for_execution,
    get_trading_loop_status,
    get_upgrade_cycle_status,
    get_watchlists,
)
from app.agents.tools.compat import (
    COMPAT_TOOL_NAMES as _COMPAT_TOOL_NAMES,  # noqa: F401 - re-exported
)
from app.agents.tools.compat import (
    get_handoff_summary,
    get_loop_cycle_summary,
    get_mcp_capabilities,
    get_mcp_tool_inventory,
    get_operational_escalation_summary,
    get_operator_decision_pack,
)
from app.agents.tools.guarded_write import (
    acknowledge_signal_handoff,
    activate_route_profile,
    append_decision_instance,
    append_review_journal_entry,
    create_inference_profile,
    deactivate_route_profile,
    run_trading_loop_once,
)

# Explicit re-export list — required so mypy accepts `from app.agents.mcp_server import X`
# across modules that import these tool functions after the MCP-module-split (Sprint 43).
__all__ = [
    # canonical read tools
    "get_action_queue_summary",
    "get_active_route_status",
    "get_alert_audit_summary",
    "get_artifact_inventory",
    "get_artifact_retention_report",
    "get_blocking_actions",
    "get_blocking_summary",
    "get_cleanup_eligibility_summary",
    "get_daily_operator_summary",
    "get_decision_journal_summary",
    "get_decision_pack_summary",
    "get_distribution_classification_report",
    "get_distribution_drift",
    "get_escalation_summary",
    "get_handoff_collector_summary",
    "get_inference_route_profile",
    "get_market_data_quote",
    "get_narrative_clusters",
    "get_operational_readiness_summary",
    "get_operator_action_summary",
    "get_operator_runbook",
    "get_paper_exposure_summary",
    "get_paper_portfolio_snapshot",
    "get_paper_positions_summary",
    "get_prioritized_actions",
    "get_protected_artifact_summary",
    "get_protective_gate_summary",
    "get_provider_health",
    "get_recent_trading_cycles",
    "get_remediation_recommendations",
    "get_research_brief",
    "get_resolution_summary",
    "get_review_journal_summary",
    "get_review_required_actions",
    "get_review_required_summary",
    "get_route_profile_report",
    "get_signal_candidates",
    "get_signals_for_execution",
    "get_trading_loop_status",
    "get_upgrade_cycle_status",
    "get_watchlists",
    # guarded write tools
    "acknowledge_signal_handoff",
    "activate_route_profile",
    "append_decision_instance",
    "append_review_journal_entry",
    "create_inference_profile",
    "deactivate_route_profile",
    "run_trading_loop_once",
    # compat tools (aliases, superseded, workflow helpers) — from tools/compat.py
    "get_mcp_capabilities",
    "get_mcp_tool_inventory",
    "get_handoff_summary",
    "get_loop_cycle_summary",
    "get_operational_escalation_summary",
    "get_operator_decision_pack",
    # server instance
    "mcp",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

mcp = FastMCP("KAI Analyst Trading Bot")

# ---------------------------------------------------------------------------
# Register canonical read tools
# ---------------------------------------------------------------------------

mcp.add_tool(get_watchlists)
mcp.add_tool(get_research_brief)
mcp.add_tool(get_signal_candidates)
mcp.add_tool(get_market_data_quote)
mcp.add_tool(get_paper_portfolio_snapshot)
mcp.add_tool(get_paper_positions_summary)
mcp.add_tool(get_paper_exposure_summary)
mcp.add_tool(get_narrative_clusters)
mcp.add_tool(get_signals_for_execution)
mcp.add_tool(get_distribution_classification_report)
mcp.add_tool(get_route_profile_report)
mcp.add_tool(get_inference_route_profile)
mcp.add_tool(get_active_route_status)
mcp.add_tool(get_upgrade_cycle_status)
mcp.add_tool(get_handoff_collector_summary)
mcp.add_tool(get_operational_readiness_summary)
mcp.add_tool(get_provider_health)
mcp.add_tool(get_distribution_drift)
mcp.add_tool(get_protective_gate_summary)
mcp.add_tool(get_remediation_recommendations)
mcp.add_tool(get_artifact_inventory)
mcp.add_tool(get_artifact_retention_report)
mcp.add_tool(get_cleanup_eligibility_summary)
mcp.add_tool(get_protected_artifact_summary)
mcp.add_tool(get_review_required_summary)
mcp.add_tool(get_escalation_summary)
mcp.add_tool(get_blocking_summary)
mcp.add_tool(get_operator_action_summary)
mcp.add_tool(get_action_queue_summary)
mcp.add_tool(get_blocking_actions)
mcp.add_tool(get_prioritized_actions)
mcp.add_tool(get_review_required_actions)
mcp.add_tool(get_decision_pack_summary)
mcp.add_tool(get_daily_operator_summary)
mcp.add_tool(get_operator_runbook)
mcp.add_tool(get_review_journal_summary)
mcp.add_tool(get_resolution_summary)
mcp.add_tool(get_alert_audit_summary)
mcp.add_tool(get_decision_journal_summary)
mcp.add_tool(get_trading_loop_status)
mcp.add_tool(get_recent_trading_cycles)

# ---------------------------------------------------------------------------
# Register guarded write tools
# ---------------------------------------------------------------------------

mcp.add_tool(create_inference_profile)
mcp.add_tool(activate_route_profile)
mcp.add_tool(deactivate_route_profile)
mcp.add_tool(acknowledge_signal_handoff)
mcp.add_tool(append_review_journal_entry)
mcp.add_tool(append_decision_instance)
mcp.add_tool(run_trading_loop_once)

# ---------------------------------------------------------------------------
# Register compat tools (aliases, superseded, workflow helpers)
# ---------------------------------------------------------------------------

mcp.add_tool(get_handoff_summary)
mcp.add_tool(get_operator_decision_pack)
mcp.add_tool(get_loop_cycle_summary)
mcp.add_tool(get_operational_escalation_summary)
mcp.add_tool(get_mcp_capabilities)


if __name__ == "__main__":
    mcp.run(transport="stdio")
