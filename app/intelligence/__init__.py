"""KAI Local Intelligence Layer (ADR 0015) — shadow-only, fail-closed.

LLM outputs are UNTRUSTED ANALYSIS: they may never trigger trades, change gates,
set env flags or deploy. ``influences_execution`` is a layer CONSTANT (false);
configuring it true is refused at settings load. No import path to or from the
execution stack exists (enforced by tests/unit/test_intelligence_layer.py).
"""

from app.intelligence.core import LLMProvider, LLMRequest, LLMResult
from app.intelligence.router import TaskRouter

__all__ = ["LLMProvider", "LLMRequest", "LLMResult", "TaskRouter"]
