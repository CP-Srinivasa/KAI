"""KAI MCP Server - server setup, tool registration, and public re-exports.

This module is intentionally thin:
- Initialises the FastMCP server instance.
- Imports tool functions from app.agents.tools.canonical_read and
  app.agents.tools.guarded_write.
- Registers every tool via mcp.add_tool() so the MCP surface is intact.
- Re-exports all tool functions and inventory helpers so existing test
  imports (from app.agents.mcp_server import ...) continue to work without
  modification.

Tool implementations live in:
  app/agents/tools/canonical_read.py  - read-only tools
  app/agents/tools/guarded_write.py   - guarded write tools
  app/agents/tools/_helpers.py        - shared path/report helpers
"""
from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from app.agents.tools._helpers import (
    ALERT_AUDIT_DEFAULT_DIR as _ALERT_AUDIT_DEFAULT_DIR,
)
from app.agents.tools._helpers import (
    ARTIFACTS_SUBDIR as _ARTIFACTS_SUBDIR,
)
from app.agents.tools._helpers import (
    HANDOFF_ACK_DEFAULT_PATH as _HANDOFF_ACK_DEFAULT_PATH,
)
from app.agents.tools._helpers import (
    LOOP_AUDIT_DEFAULT_PATH as _LOOP_AUDIT_DEFAULT_PATH,
)
from app.agents.tools._helpers import (
    WORKSPACE_ROOT as _WORKSPACE_ROOT,  # noqa: F401 - re-exported for test monkeypatching
)
from app.agents.tools.canonical_read import (
    CANONICAL_READ_TOOL_NAMES as _CANONICAL_MCP_READ_TOOL_NAMES,  # noqa: N811
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
from app.agents.tools.guarded_write import (
    GUARDED_WRITE_TOOL_NAMES as _GUARDED_MCP_WRITE_TOOL_NAMES,  # noqa: N811
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
from app.research.active_route import DEFAULT_ACTIVE_ROUTE_PATH as _DEFAULT_ACTIVE_ROUTE_PATH

logger = logging.getLogger(__name__)

_ACTIVE_ROUTE_PATH_STR = str(_DEFAULT_ACTIVE_ROUTE_PATH)

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

mcp = FastMCP("KAI Analyst Trading Bot")

# ---------------------------------------------------------------------------
# Inventory metadata (kept here for get_mcp_tool_inventory and contract tests)
# ---------------------------------------------------------------------------

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
# Compatibility aliases (backward-compat tools with distinct registered names)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_handoff_summary(
    handoff_path: str,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
) -> dict[str, object]:
    """Backward-compatible alias for the canonical collector summary surface."""
    return await get_handoff_collector_summary(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
    )


@mcp.tool()
async def get_operator_decision_pack(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = _ACTIVE_ROUTE_PATH_STR,
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str = _ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Backward-compatible alias for the canonical decision-pack summary."""
    return await get_decision_pack_summary(
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
async def get_loop_cycle_summary(
    audit_path: str = _LOOP_AUDIT_DEFAULT_PATH,
    last_n: int = 20,
) -> dict[str, object]:
    """Compatibility alias for get_recent_trading_cycles."""
    return await get_recent_trading_cycles(
        audit_path=audit_path,
        last_n=last_n,
    )


# ---------------------------------------------------------------------------
# Superseded tools (kept for compatibility, route to canonical replacements)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_operational_escalation_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
    state_path: str = _ACTIVE_ROUTE_PATH_STR,
    abc_output_path: str | None = None,
    alert_audit_dir: str = _ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Return the operator-facing escalation summary derived from the canonical readiness report.

    Read-only surface (Sprint 27). No execution, no write-back, no auto-remediation.
    escalation_status: nominal / elevated / critical.
    I-169-I-176.
    """
    return await get_escalation_summary(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
        state_path=state_path,
        abc_output_path=abc_output_path,
        alert_audit_dir=alert_audit_dir,
        stale_after_hours=stale_after_hours,
    )


# ---------------------------------------------------------------------------
# Workflow helper
# ---------------------------------------------------------------------------


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
                "Acknowledgement is audit-only - not write-back or execution trigger (I-116)",
                "Readiness summary is observational only - no auto-remediation",
                "Protective Gates are entirely read-only and advisory (I-123)",
                "Cleanup eligibility is advisory only - no auto-deletion",
                "No trading execution",
            ],
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
