"""Factory for instantiating analysis providers.

Provider tiers:
  Tier 1 (rule-based)  — embedded in AnalysisPipeline._build_fallback_analysis()
  Tier 2  (internal)   — InternalModelProvider — rule heuristics, zero deps, always available
  Tier 3  (external)   — OpenAI, Anthropic, Gemini — premium LLM, needs API key
"""

from typing import Any

from app.analysis.base.interfaces import BaseAnalysisProvider


def create_provider(
    provider_type: str,
    settings: Any,
    *,
    apply_inference_mode: bool = True,
) -> BaseAnalysisProvider | None:
    """Instantiate the requested analysis provider from settings.

    Args:
        provider_type:
            'internal'  — InternalModelProvider (rule heuristics, no API key, always available)
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
        provider: BaseAnalysisProvider | None = InternalModelProvider(keyword_engine)
        return _maybe_wrap_inference(provider, settings) if apply_inference_mode else provider

    if provider_type == "openai":
        provider = None
        if settings.providers.openai_api_key:
            from app.integrations.openai.provider import OpenAIAnalysisProvider

            provider = OpenAIAnalysisProvider.from_settings(settings.providers)
        return _maybe_wrap_inference(provider, settings) if apply_inference_mode else provider

    if provider_type in ("anthropic", "claude"):
        provider = None
        if settings.providers.anthropic_api_key:
            from app.integrations.anthropic.provider import AnthropicAnalysisProvider

            provider = AnthropicAnalysisProvider.from_settings(settings.providers)
        return _maybe_wrap_inference(provider, settings) if apply_inference_mode else provider

    if provider_type == "gemini":
        provider = None
        if settings.providers.gemini_api_key:
            from app.integrations.gemini.provider import GeminiAnalysisProvider

            provider = GeminiAnalysisProvider.from_settings(settings.providers)
        return _maybe_wrap_inference(provider, settings) if apply_inference_mode else provider

    if provider_type == "ensemble":
        from app.analysis.ensemble.provider import EnsembleProvider

        providers = []

        if getattr(settings.providers, "openai_api_key", None):
            providers.append(create_provider("openai", settings, apply_inference_mode=False))
        if getattr(settings.providers, "anthropic_api_key", None):
            providers.append(create_provider("anthropic", settings, apply_inference_mode=False))
        if getattr(settings.providers, "gemini_api_key", None):
            providers.append(create_provider("gemini", settings, apply_inference_mode=False))

        # D-174 Phase I: Grok as emergency fallback — only when all premium
        # providers above have failed. Flag-gated so the chain stays unchanged
        # when disabled.
        if getattr(settings.providers, "xai_fallback_enabled", False) and getattr(
            settings.providers, "xai_api_key", None
        ):
            providers.append(create_provider("grok", settings, apply_inference_mode=False))

        # Internal model must be last (guaranteed fallback)
        internal = create_provider("internal", settings, apply_inference_mode=False)
        if internal:
            providers.append(internal)

        ensemble = EnsembleProvider([p for p in providers if p is not None])
        return _maybe_wrap_inference(ensemble, settings) if apply_inference_mode else ensemble

    if provider_type == "grok":
        provider = None
        if getattr(settings.providers, "xai_api_key", None):
            from app.integrations.xai.provider import GrokAnalysisProvider

            provider = GrokAnalysisProvider.from_settings(settings.providers)
        return _maybe_wrap_inference(provider, settings) if apply_inference_mode else provider

    raise ValueError(f"Unsupported analysis provider: {provider_type!r}")


def _maybe_wrap_inference(
    provider: BaseAnalysisProvider | None,
    settings: Any,
) -> BaseAnalysisProvider | None:
    from app.core.settings import InferenceSettings
    from app.inference.analysis_provider import wrap_analysis_provider

    inference = getattr(settings, "inference", None)
    # Preserve compatibility for isolated factory callers/test doubles that
    # predate AppSettings.inference; only the explicit typed profile activates it.
    if not isinstance(inference, InferenceSettings):
        return provider
    return wrap_analysis_provider(provider, inference)


def create_cli_primary_provider() -> BaseAnalysisProvider | None:
    """Primary chain for CLI/ingestion pipelines: OpenAI -> Gemini -> (Grok).

    Deliberately EXCLUDES Anthropic (reserved as independent shadow, see
    :func:`create_shadow_provider`) and the internal fallback (``None`` means
    "no external LLM configured" and lets callers run rule-based). Single
    source of truth since 2026-07-11 (Audit F-4) — previously duplicated in
    ``app/cli/main.py``.
    """
    from app.core.settings import get_settings

    settings = get_settings()
    providers: list[BaseAnalysisProvider] = []
    if settings.providers.openai_api_key:
        from app.integrations.openai.provider import OpenAIAnalysisProvider

        providers.append(OpenAIAnalysisProvider.from_settings(settings.providers))
    if settings.providers.gemini_api_key:
        from app.integrations.gemini.provider import GeminiAnalysisProvider

        providers.append(GeminiAnalysisProvider.from_settings(settings.providers))
    if settings.providers.xai_fallback_enabled and settings.providers.xai_api_key:
        from app.integrations.xai.provider import GrokAnalysisProvider

        providers.append(GrokAnalysisProvider.from_settings(settings.providers))
    if not providers:
        return _maybe_wrap_inference(None, settings)
    if len(providers) == 1:
        return _maybe_wrap_inference(providers[0], settings)
    from app.analysis.ensemble.provider import EnsembleProvider

    return _maybe_wrap_inference(EnsembleProvider(providers), settings)


def create_shadow_provider() -> BaseAnalysisProvider | None:
    """Independent shadow analyst: prefer Anthropic, else Gemini, else None."""
    from app.core.settings import get_settings

    settings = get_settings()
    if settings.providers.anthropic_api_key:
        from app.integrations.anthropic.provider import AnthropicAnalysisProvider

        return AnthropicAnalysisProvider.from_settings(settings.providers)
    if settings.providers.gemini_api_key:
        from app.integrations.gemini.provider import GeminiAnalysisProvider

        return GeminiAnalysisProvider.from_settings(settings.providers)
    return None
