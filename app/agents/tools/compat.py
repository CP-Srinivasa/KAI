"""Compatibility aliases, superseded tools, and workflow helpers for the MCP surface.

These functions are plain async callables (no @mcp.tool decorator).
Registration happens in app/agents/mcp_server.py via mcp.add_tool().

Tool classes:
  compatibility_alias  — backward-compat names that delegate to canonical tools
  superseded           — older tool names kept for external callers, route to replacement
  workflow_helper      — meta / capability discovery tools
"""

from __future__ import annotations

import json

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
from app.agents.tools.canonical_read import (
    CANONICAL_READ_TOOL_NAMES,
    get_decision_pack_summary,
    get_escalation_summary,
    get_handoff_collector_summary,
    get_recent_trading_cycles,
)
from app.agents.tools.guarded_write import GUARDED_WRITE_TOOL_NAMES

_ACTIVE_ROUTE_PATH_STR = "artifacts/active_route_state.json"

# ---------------------------------------------------------------------------
# Inventory metadata
# ---------------------------------------------------------------------------

_MCP_WORKFLOW_HELPER_NAMES: tuple[str, ...] = ("get_mcp_capabilities",)

_MCP_TOOL_ALIASES: dict[str, dict[str, str]] = {
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

_SUPERSEDED_MCP_TOOLS: dict[str, dict[str, str]] = {
    "get_operational_escalation_summary": {
        "replacement_tool": "get_escalation_summary",
        "tool_class": "read_only",
        "status": "superseded",
    }
}

# Exported name tuple for registration bookkeeping
COMPAT_TOOL_NAMES: tuple[str, ...] = (
    "get_handoff_summary",
    "get_loop_cycle_summary",
    "get_mcp_capabilities",
    "get_operational_escalation_summary",
    "get_operator_decision_pack",
)


def get_mcp_tool_inventory() -> dict[str, object]:
    """Return the canonical MCP inventory used by capabilities and contract tests."""
    return {
        "canonical_read_tools": list(CANONICAL_READ_TOOL_NAMES),
        "guarded_write_tools": list(GUARDED_WRITE_TOOL_NAMES),
        "workflow_helpers": list(_MCP_WORKFLOW_HELPER_NAMES),
        "aliases": {tool_name: dict(metadata) for tool_name, metadata in _MCP_TOOL_ALIASES.items()},
        "superseded_tools": {
            tool_name: dict(metadata) for tool_name, metadata in _SUPERSEDED_MCP_TOOLS.items()
        },
    }


# ---------------------------------------------------------------------------
# Compatibility aliases
# ---------------------------------------------------------------------------


async def get_handoff_summary(
    handoff_path: str,
    acknowledgement_path: str = _HANDOFF_ACK_DEFAULT_PATH,
) -> dict[str, object]:
    """Backward-compatible alias for the canonical collector summary surface."""
    return await get_handoff_collector_summary(
        handoff_path=handoff_path,
        acknowledgement_path=acknowledgement_path,
    )


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
# Superseded tools
# ---------------------------------------------------------------------------


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
