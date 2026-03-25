"""Compatibility aliases, superseded tools, and workflow helpers for the MCP surface.

These functions are plain async callables (no @mcp.tool decorator).
Registration happens in app/agents/mcp_server.py via mcp.add_tool().

Tool classes:
  workflow_helper      — meta / capability discovery tools
  compatibility_alias  — backward-compat names that delegate to canonical tools
"""

from __future__ import annotations

import json

from app.agents.tools._helpers import (
    LOOP_AUDIT_DEFAULT_PATH as _LOOP_AUDIT_DEFAULT_PATH,
)
from app.agents.tools.canonical_read import (
    CANONICAL_READ_TOOL_NAMES,
    get_recent_trading_cycles,
)
from app.agents.tools.guarded_write import GUARDED_WRITE_TOOL_NAMES

# ---------------------------------------------------------------------------
# Inventory metadata
# ---------------------------------------------------------------------------

_MCP_WORKFLOW_HELPER_NAMES: tuple[str, ...] = ("get_mcp_capabilities",)

_MCP_TOOL_ALIASES: dict[str, dict[str, str]] = {
    "get_loop_cycle_summary": {
        "canonical_tool": "get_recent_trading_cycles",
        "tool_class": "read_only",
        "status": "compatibility_alias",
    },
}

# Exported name tuple for registration bookkeeping
COMPAT_TOOL_NAMES: tuple[str, ...] = (
    "get_loop_cycle_summary",
    "get_mcp_capabilities",
)


def get_mcp_tool_inventory() -> dict[str, object]:
    """Return the canonical MCP inventory used by capabilities and contract tests."""
    return {
        "canonical_read_tools": list(CANONICAL_READ_TOOL_NAMES),
        "guarded_write_tools": list(GUARDED_WRITE_TOOL_NAMES),
        "workflow_helpers": list(_MCP_WORKFLOW_HELPER_NAMES),
        "aliases": {tool_name: dict(metadata) for tool_name, metadata in _MCP_TOOL_ALIASES.items()},
    }


# ---------------------------------------------------------------------------
# Compatibility aliases
# ---------------------------------------------------------------------------


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
            "guardrails": [
                "Write paths restricted to workspace/artifacts/ (I-95)",
                "Write audit JSONL appended for every write call (I-94)",
                "No APP_LLM_PROVIDER mutation",
                "No auto-routing or auto-promotion",
                "No direct execution hook for signals",
                "Trading loop control is explicit run-once only (no daemon/autopilot)",
                "No trading execution",
            ],
        },
        indent=2,
    )
