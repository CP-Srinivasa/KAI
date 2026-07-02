"""EnsembleProvider — ordered provider selection with guaranteed fallback.

Strategy (current): try providers in priority order, use first that succeeds.
The InternalModelProvider MUST be included as the last entry to ensure the
ensemble never returns None or raises due to missing API keys.

Future evolution:
- Collect outputs from multiple providers simultaneously
- Compare/score for distillation corpus
- Blend results weighted by per-provider calibration

Usage:
    internal = InternalModelProvider(keyword_engine)
    openai = OpenAIAnalysisProvider.from_settings(settings.providers)
    ensemble = EnsembleProvider(providers=[openai, internal])
    # ^ tries openai first, falls back to internal if it fails or is absent
"""

from __future__ import annotations

from typing import Any

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.core.logging import get_logger

logger = get_logger(__name__)


class EnsembleProvider(BaseAnalysisProvider):
    """Try providers in order, use the first one that succeeds.

    The last provider in the list acts as the guaranteed fallback.
    Always include an InternalModelProvider as the final entry.

    Attributes:
        providers: Ordered list — index 0 = highest priority (external premium).
                   Last entry = fallback (internal, always available).
    """

    def __init__(self, providers: list[BaseAnalysisProvider]) -> None:
        if not providers:
            raise ValueError("EnsembleProvider requires at least one provider.")
        self._providers = providers
        self._active_provider_name: str = providers[-1].provider_name

    @property
    def provider_name(self) -> str:
        return f"ensemble({','.join(p.provider_name for p in self._providers)})"

    @property
    def model(self) -> str | None:
        return self._active_provider_name

    @property
    def active_provider_name(self) -> str:
        """Return the provider that actually produced the latest result."""
        return self._active_provider_name

    @property
    def provider_chain(self) -> list[str]:
        """Ordered technical trace of configured providers."""
        return [provider.provider_name for provider in self._providers]

    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        """Try each provider in order, return the first successful result."""
        last_error: Exception | None = None

        for provider in self._providers:
            try:
                result = await provider.analyze(title, text, context)
                # Per-call annotation survives concurrent dispatch where the
                # shared _active_provider_name would race. Keep the instance
                # state updated for back-compat callers, but the Pipeline uses
                # result.provider_used as the source of truth.
                if result.provider_used is None:
                    result.provider_used = provider.provider_name
                self._active_provider_name = provider.provider_name
                logger.debug(
                    "ensemble_provider_selected",
                    provider=provider.provider_name,
                )
                return result
            except Exception as exc:
                logger.warning(
                    "ensemble_provider_failed",
                    provider=provider.provider_name,
                    error=str(exc),
                )
                last_error = exc
                continue

        # All providers failed — this should not happen if InternalModelProvider is last
        raise RuntimeError(
            f"All ensemble providers failed. Last error: {last_error}"
        ) from last_error
