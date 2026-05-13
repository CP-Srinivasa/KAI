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
  app/agents/tools/compat.py          - aliases and workflow helpers
  app/agents/tools/_helpers.py        - shared path/report helpers
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from app.agents.tools._helpers import (
    WORKSPACE_ROOT as _WORKSPACE_ROOT,  # noqa: F401 - re-exported for test monkeypatching
)
from app.agents.tools.canonical_read import (
    get_alert_audit_summary,
    get_daily_operator_summary,
    get_decision_journal_summary,
    get_market_data_quote,
    get_narrative_clusters,
    get_paper_exposure_summary,
    get_paper_portfolio_snapshot,
    get_paper_positions_summary,
    get_recent_trading_cycles,
    get_research_brief,
    get_signal_candidates,
    get_signals_for_execution,
    get_trading_loop_status,
    get_watchlists,
)
from app.agents.tools.compat import (
    COMPAT_TOOL_NAMES as _COMPAT_TOOL_NAMES,  # noqa: F401 - re-exported
)
from app.agents.tools.compat import (
    get_loop_cycle_summary,
    get_mcp_capabilities,
    get_mcp_tool_inventory,
)
from app.agents.tools.guarded_write import (
    append_decision_instance,
    run_trading_loop_once,
)

# Explicit re-export list — required so mypy accepts `from app.agents.mcp_server import X`
# across modules that import these tool functions after the MCP-module-split (Sprint 43).
__all__ = [
    # canonical read tools
    "get_alert_audit_summary",
    "get_daily_operator_summary",
    "get_decision_journal_summary",
    "get_market_data_quote",
    "get_narrative_clusters",
    "get_paper_exposure_summary",
    "get_paper_portfolio_snapshot",
    "get_paper_positions_summary",
    "get_recent_trading_cycles",
    "get_research_brief",
    "get_signal_candidates",
    "get_signals_for_execution",
    "get_trading_loop_status",
    "get_watchlists",
    # guarded write tools
    "append_decision_instance",
    "run_trading_loop_once",
    # compat tools — from tools/compat.py
    "get_mcp_capabilities",
    "get_mcp_tool_inventory",
    "get_loop_cycle_summary",
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
mcp.add_tool(get_daily_operator_summary)
mcp.add_tool(get_alert_audit_summary)
mcp.add_tool(get_decision_journal_summary)
mcp.add_tool(get_trading_loop_status)
mcp.add_tool(get_recent_trading_cycles)

# ---------------------------------------------------------------------------
# Register guarded write tools
# ---------------------------------------------------------------------------

mcp.add_tool(append_decision_instance)
mcp.add_tool(run_trading_loop_once)

# ---------------------------------------------------------------------------
# Register compat tools (aliases, workflow helpers)
# ---------------------------------------------------------------------------

mcp.add_tool(get_loop_cycle_summary)
mcp.add_tool(get_mcp_capabilities)


if __name__ == "__main__":
    mcp.run(transport="stdio")
