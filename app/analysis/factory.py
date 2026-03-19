"""Factory for instantiating analysis providers.

Provider tiers:
  Tier 1 (rule-based)  — embedded in AnalysisPipeline._build_fallback_analysis()
  Tier 2a (internal)   — InternalModelProvider — rule heuristics, zero deps, always available
  Tier 2b (companion)  — InternalCompanionProvider — HTTP to local model (localhost only)
  Tier 3 (external)    — OpenAI, Anthropic, Gemini — premium LLM, needs API key
"""

from typing import Any

from app.analysis.base.interfaces import BaseAnalysisProvider


def create_provider(provider_type: str, settings: Any) -> BaseAnalysisProvider | None:
    """Instantiate the requested analysis provider from settings.

    Args:
        provider_type:
            'internal'  — InternalModelProvider (rule heuristics, no API key, always available)
            'companion' — InternalCompanionProvider (HTTP to localhost model endpoint)
            'openai'    — OpenAI GPT provider
            'anthropic' / 'claude' — Anthropic Claude provider
            'gemini'    — Google Gemini provider
        settings: AppSettings — must have a .providers and .monitor_dir attribute.

    Returns:
        BaseAnalysisProvider instance, or None if required configuration is missing.
        'internal' always returns an instance (never None).

    Raises:
        ValueError: If provider_type is not supported.
    """
    if provider_type == "internal":
        from pathlib import Path

        from app.analysis.internal_model.provider import InternalModelProvider
        from app.analysis.keywords.engine import KeywordEngine

        keyword_engine = KeywordEngine.from_monitor_dir(Path(settings.monitor_dir))
        return InternalModelProvider(keyword_engine)

    if provider_type == "companion":
        endpoint = getattr(settings.providers, "companion_model_endpoint", None)
        if not endpoint:
            return None
        from app.analysis.providers.companion import InternalCompanionProvider

        return InternalCompanionProvider(
            endpoint=endpoint,
            model=getattr(settings.providers, "companion_model_name", "local-model"),
            timeout=getattr(settings.providers, "companion_model_timeout", 10),
        )

    if provider_type == "openai":
        if not settings.providers.openai_api_key:
            return None
        from app.integrations.openai.provider import OpenAIAnalysisProvider

        return OpenAIAnalysisProvider.from_settings(settings.providers)

    if provider_type in ("anthropic", "claude"):
        if not settings.providers.anthropic_api_key:
            return None
        from app.integrations.anthropic.provider import AnthropicAnalysisProvider

        return AnthropicAnalysisProvider.from_settings(settings.providers)

    if provider_type == "gemini":
        if not settings.providers.gemini_api_key:
            return None
        from app.integrations.gemini.provider import GeminiAnalysisProvider

        return GeminiAnalysisProvider.from_settings(settings.providers)

    raise ValueError(f"Unsupported analysis provider: {provider_type!r}")
