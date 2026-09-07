"""Factory for instantiating analysis providers.

Provider tiers:
  Tier 1 (rule-based)  — embedded in AnalysisPipeline._build_fallback_analysis()
  Tier 2  (internal)   — InternalModelProvider — rule heuristics, zero deps, always available
  Tier 3  (external)   — OpenAI, Anthropic, Gemini — premium LLM, needs API key
"""

from typing import Any

from app.ai.config import InferenceSettings
from app.analysis.base.interfaces import BaseAnalysisProvider


def _wire_control_plane(
    provider: BaseAnalysisProvider | None,
    settings: Any,
    *,
    force_off: bool = False,
) -> BaseAnalysisProvider | None:
    if provider is None:
        return None
    from app.core.settings import AppSettings

    if not isinstance(settings, AppSettings):
        return provider
    gateway_settings = InferenceSettings()
    from app.analysis.ai_control_plane import ControlPlaneAnalysisProvider

    return ControlPlaneAnalysisProvider(provider, gateway_settings, force_off=force_off)


def create_provider(
    provider_type: str,
    settings: Any,
    *,
    _wire: bool = True,
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
        return InternalModelProvider(keyword_engine)

    if provider_type == "openai":
        if not settings.providers.openai_api_key:
            return None
        from app.integrations.openai.provider import OpenAIAnalysisProvider

        openai_provider = OpenAIAnalysisProvider.from_settings(settings.providers)
        return _wire_control_plane(openai_provider, settings) if _wire else openai_provider

    if provider_type in ("anthropic", "claude"):
        if not settings.providers.anthropic_api_key:
            return None
        from app.integrations.anthropic.provider import AnthropicAnalysisProvider

        anthropic_provider = AnthropicAnalysisProvider.from_settings(settings.providers)
        return _wire_control_plane(anthropic_provider, settings) if _wire else anthropic_provider

    if provider_type == "gemini":
        if not settings.providers.gemini_api_key:
            return None
        from app.integrations.gemini.provider import GeminiAnalysisProvider

        gemini_provider = GeminiAnalysisProvider.from_settings(settings.providers)
        return _wire_control_plane(gemini_provider, settings) if _wire else gemini_provider

    if provider_type == "ensemble":
        from app.analysis.ensemble.provider import EnsembleProvider

        providers = []

        if getattr(settings.providers, "openai_api_key", None):
            providers.append(create_provider("openai", settings, _wire=False))
        if getattr(settings.providers, "anthropic_api_key", None):
            providers.append(create_provider("anthropic", settings, _wire=False))
        if getattr(settings.providers, "gemini_api_key", None):
            providers.append(create_provider("gemini", settings, _wire=False))

        # D-174 Phase I: Grok as emergency fallback — only when all premium
        # providers above have failed. Flag-gated so the chain stays unchanged
        # when disabled.
        if getattr(settings.providers, "xai_fallback_enabled", False) and getattr(
            settings.providers, "xai_api_key", None
        ):
            providers.append(create_provider("grok", settings, _wire=False))

        # Internal model must be last (guaranteed fallback)
        internal = create_provider("internal", settings)
        if internal:
            providers.append(internal)

        ensemble = EnsembleProvider([p for p in providers if p is not None])
        return _wire_control_plane(ensemble, settings) if _wire else ensemble

    if provider_type == "grok":
        if not getattr(settings.providers, "xai_api_key", None):
            return None
        from app.integrations.xai.provider import GrokAnalysisProvider

        grok_provider = GrokAnalysisProvider.from_settings(settings.providers)
        return _wire_control_plane(grok_provider, settings) if _wire else grok_provider

    raise ValueError(f"Unsupported analysis provider: {provider_type!r}")


def describe_primary_chain(settings: Any) -> list[str]:
    """Ordered provider names of the primary chain — WITHOUT constructing clients.

    Same predicate order as :func:`create_cli_primary_provider`, which now
    builds from this list. Keeping one definition means /health/ai cannot
    report a chain that differs from the one actually built (NEO-P-005), and
    a health probe never instantiates an SDK client or touches an API key.
    """
    providers = settings.providers
    names: list[str] = []
    if getattr(providers, "openai_api_key", None):
        names.append("openai")
    if getattr(providers, "gemini_api_key", None):
        names.append("gemini")
    if getattr(providers, "xai_fallback_enabled", False) and getattr(
        providers, "xai_api_key", None
    ):
        names.append("grok")
    return names


def describe_shadow_chain(settings: Any) -> list[str]:
    """Provider names the shadow analyst would use, in preference order."""
    providers = settings.providers
    if getattr(providers, "anthropic_api_key", None):
        return ["anthropic"]
    if getattr(providers, "gemini_api_key", None):
        return ["gemini"]
    return []


def create_primary_provider() -> BaseAnalysisProvider | None:
    """THE primary chain for every entry point: OpenAI -> Gemini -> (Grok).

    Deliberately EXCLUDES Anthropic (reserved as independent shadow, see
    :func:`create_shadow_provider`) and the internal fallback (``None`` means
    "no external LLM configured" and lets callers run rule-based). Single
    source of truth since 2026-07-11 (Audit F-4) — previously duplicated in
    ``app/cli/main.py``. Order comes from :func:`describe_primary_chain`.

    Renamed from ``create_cli_primary_provider`` (NEO-P-003, 2026-09-02): the
    server path uses it too now, so "cli" was simply wrong. The old name stays
    as an alias below.
    """
    from app.core.settings import get_settings

    settings = get_settings()
    providers = [
        provider
        for provider in (
            create_provider(name, settings, _wire=False)
            for name in describe_primary_chain(settings)
        )
        if provider is not None
    ]
    if not providers:
        return None
    if len(providers) == 1:
        return _wire_control_plane(providers[0], settings)
    from app.analysis.ensemble.provider import EnsembleProvider

    return _wire_control_plane(EnsembleProvider(providers), settings)


# Back-compat alias — app/cli/main.py and external callers keep working.
create_cli_primary_provider = create_primary_provider


def create_shadow_provider() -> BaseAnalysisProvider | None:
    """Independent shadow analyst: prefer Anthropic, else Gemini, else None."""
    from app.core.settings import get_settings

    settings = get_settings()
    for name in describe_shadow_chain(settings):
        provider = create_provider(name, settings, _wire=False)
        if provider is not None:
            return _wire_control_plane(provider, settings, force_off=True)
    return None
