"""Factory for instantiating analysis providers."""

from typing import Any

from app.analysis.base.interfaces import BaseAnalysisProvider


def create_provider(provider_type: str, settings: Any) -> BaseAnalysisProvider | None:
    """Instantiate the requested LLM provider from settings.

    Args:
        provider_type: 'openai', 'anthropic' (alias 'claude'), or 'gemini'
        settings: AppSettings — must have a .providers attribute (ProviderSettings).

    Returns:
        BaseAnalysisProvider instance, or None if the API key is missing.

    Raises:
        ValueError: If provider_type is not supported.
    """
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
