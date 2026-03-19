"""Ensemble provider — multi-tier provider orchestration.

Implements the three-tier analysis strategy:
  Tier 1: Rule-based fallback (embedded in AnalysisPipeline)
  Tier 2: InternalModelProvider (always available, no API key)
  Tier 3: External providers (OpenAI, Anthropic, Gemini)

EnsembleProvider selects the best available provider at analysis time.
Current strategy: try external providers first, fall back to internal.
Future strategy: blend/compare outputs for distillation.

Contract:
- Always returns a result (never None)
- provider_name reflects which provider actually produced the result
- Falls back gracefully without raising
"""

from app.analysis.ensemble.provider import EnsembleProvider

__all__ = ["EnsembleProvider"]
