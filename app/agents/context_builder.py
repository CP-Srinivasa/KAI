"""
Agent Context Builder
======================
Assembles a structured context package from research packs and signal data.
This context is consumed by AI agents (Claude, OpenAI assistants, or MCP clients).

The context is designed to be:
  - Compact: fits within typical LLM context windows
  - Informative: contains all relevant signals, risks, and narratives
  - Structured: JSON-serializable for easy agent consumption

Usage:
    builder = AgentContextBuilder()
    context = builder.build(query="Bitcoin ETF impact on BTC price")
    # Pass context["system_prompt"] + context["data"] to your LLM agent
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_SIGNALS_IN_CONTEXT = 8
_MAX_NARRATIVES_IN_CONTEXT = 3
_MAX_ANALOGUES_IN_CONTEXT = 2


class AgentContextBuilder:
    """
    Builds agent context from available research data.

    The context includes:
      - System instructions (role, constraints, tool list)
      - Current date/time
      - Top signal candidates
      - Active narrative clusters
      - Historical analogues for relevant assets
      - Watchlist hits
    """

    def __init__(self, tool_names: list[str] | None = None) -> None:
        self._tool_names = tool_names or []

    def build(
        self,
        query: str = "",
        assets: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Build agent context for a given query/asset focus.

        Args:
            query:  Natural language query or instruction
            assets: Specific assets to focus on (optional)

        Returns:
            dict with: system_prompt, data, tools_available, generated_at
        """
        from app.research.router_helpers import get_sample_candidates
        from app.research.builder import ResearchPackBuilder
        from app.analysis.historical.matcher import HistoricalMatcher

        candidates = get_sample_candidates()
        builder = ResearchPackBuilder()
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

        # Filter to relevant assets if specified
        relevant_candidates = candidates
        if assets:
            asset_set = {a.upper() for a in assets}
            relevant_candidates = [c for c in candidates if c.asset in asset_set] or candidates

        # Build brief
        brief = builder.daily_brief(relevant_candidates, date=date_str)

        # Historical analogues for top assets
        matcher = HistoricalMatcher()
        analogues_by_asset: dict[str, list[dict[str, Any]]] = {}
        for asset_pack in brief.top_assets[:3]:
            analogues = matcher.find(assets=[asset_pack.asset], max_results=_MAX_ANALOGUES_IN_CONTEXT)
            if analogues:
                analogues_by_asset[asset_pack.asset] = [a.to_dict() for a in analogues]

        # Compact data payload
        data: dict[str, Any] = {
            "date": date_str,
            "query": query,
            "market_sentiment": brief.market_sentiment,
            "overall_urgency": brief.overall_urgency,
            "total_signals": brief.total_signals,
            "key_themes": brief.key_themes,
            "risk_summary": brief.risk_summary,
            "top_signals": [
                {
                    "asset": c.asset,
                    "direction": c.direction_hint.value,
                    "confidence": round(c.confidence, 2),
                    "urgency": c.urgency.value,
                    "narrative": c.narrative_label.value,
                    "title": c.title,
                    "next_step": c.recommended_next_step,
                }
                for c in relevant_candidates[:_MAX_SIGNALS_IN_CONTEXT]
            ],
            "active_narratives": [
                {
                    "label": n.narrative_label,
                    "assets": n.affected_assets,
                    "direction": n.dominant_direction,
                    "confidence": round(n.overall_confidence, 2),
                }
                for n in brief.active_narratives[:_MAX_NARRATIVES_IN_CONTEXT]
            ],
            "historical_analogues": analogues_by_asset,
        }

        system_prompt = self._build_system_prompt(query)

        logger.info(
            "agent_context_built",
            query=query[:50] if query else "",
            signals=len(data["top_signals"]),
            assets=list(analogues_by_asset.keys()),
        )

        return {
            "system_prompt": system_prompt,
            "data": data,
            "tools_available": self._tool_names,
            "generated_at": datetime.utcnow().isoformat(),
        }

    def _build_system_prompt(self, query: str) -> str:
        tools_note = ""
        if self._tool_names:
            tools_note = (
                f"\n\nAvailable tools: {', '.join(self._tool_names)}. "
                "Use tools to fetch additional data when needed."
            )

        return f"""You are an AI research analyst specializing in crypto and financial markets.
You have access to real-time signal data, narrative clusters, and historical analogues.

IMPORTANT CONSTRAINTS:
- You provide RESEARCH and ANALYSIS only. You do NOT place trades or recommend specific position sizes.
- Always include risk caveats in your responses.
- Historical analogues are context, not predictions.
- Clearly state your confidence level for any assessment.
- If data is insufficient, say so rather than speculating.

Current query context: {query or 'General market overview'}{tools_note}

Respond in a structured, concise manner. Lead with the most important findings."""
