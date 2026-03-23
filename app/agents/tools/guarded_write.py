"""Guarded write MCP tool inventory.

This module defines the authoritative list of write-guarded MCP tools.
These tools perform controlled writes to append-only artifact files.
All writes are audit-logged via the mcp_write_audit.jsonl trail.

Invariants enforced by all guarded-write tools:
- execution_enabled: False — no live trading orders are created
- write_back_allowed: False — no external state mutation beyond artifacts/
- All paths are restricted to workspace/artifacts/ (I-95 write guard)
- All writes are appended to immutable audit trails

The tool implementations are registered in app.agents.mcp_server via @mcp.tool().
This module exports the inventory for contract tests and documentation.

Tool list:
- create_inference_profile: Create a new InferenceRouteProfile artifact
- activate_route_profile: Activate a route profile state file
- deactivate_route_profile: Deactivate the active route profile
- acknowledge_signal_handoff: Acknowledge a signal handoff record
- append_review_journal_entry: Append an operator review journal entry
- append_decision_instance: Append a validated decision instance to the journal
- run_trading_loop_once: Run one guarded paper/shadow trading cycle
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Guarded write inventory (mirrors _GUARDED_MCP_WRITE_TOOL_NAMES in mcp_server)
# ---------------------------------------------------------------------------

GUARDED_WRITE_TOOL_NAMES: tuple[str, ...] = (
    "create_inference_profile",
    "activate_route_profile",
    "deactivate_route_profile",
    "acknowledge_signal_handoff",
    "append_review_journal_entry",
    "append_decision_instance",
    "run_trading_loop_once",
)


def get_guarded_write_tool_names() -> tuple[str, ...]:
    """Return the locked guarded-write tool name tuple."""
    return GUARDED_WRITE_TOOL_NAMES
