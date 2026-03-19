"""Internal model provider — Tier 2 analyst.

Always available, no API key required. Implements BaseAnalysisProvider so it can
be swapped into any pipeline position without code changes.

Current implementation: deterministic rule-based heuristics (stub-grade).
Future: plug in a fine-tuned local model behind the same interface.

Contract:
- priority ceiling: ≤ 5 (conservative, I-13)
- actionable: always False (human review required for Tier 2)
- provider_name: "internal"
- never raises unless KeywordEngine is broken
"""

from app.analysis.internal_model.provider import InternalModelProvider

__all__ = ["InternalModelProvider"]
