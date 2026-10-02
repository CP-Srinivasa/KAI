from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from app.core.enums import MarketScope, SentimentLabel


class LLMAnalysisOutput(BaseModel):
    # Required configuration for strict validation
    model_config = ConfigDict(strict=True, validate_assignment=True)

    sentiment_label: SentimentLabel
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    relevance_score: float = Field(ge=0.0, le=1.0)
    impact_score: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    novelty_score: float = Field(ge=0.0, le=1.0)
    spam_probability: float = Field(ge=0.0, le=1.0)

    market_scope: MarketScope = MarketScope.UNKNOWN
    affected_assets: list[str] = Field(default_factory=list)
    affected_sectors: list[str] = Field(default_factory=list)
    event_type: str | None = None

    short_reasoning: str | None = None
    long_reasoning: str | None = None
    bull_case: str | None = None
    bear_case: str | None = None
    neutral_case: str | None = None

    directional_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    event_timing: str | None = None

    historical_analogs: list[str] = Field(default_factory=list)
    recommended_priority: int = Field(default=5, ge=1, le=10)
    actionable: bool = False
    tags: list[str] = Field(default_factory=list)

    # Set by ensemble/fallback wrappers to identify which underlying provider
    # actually produced this output. Per-call, so it survives parallel runs
    # where a shared provider instance would otherwise race on mutable state.
    provider_used: str | None = None

    # Cognitive Audit fields (populated post-validation by the provider)
    raw_prompt: str | None = None
    raw_response: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    # Welcher Transport die Antwort WIRKLICH lieferte -- gesetzt von der
    # Steuerebene (``app/analysis/ai_control_plane.py``), gelesen von der
    # aeusseren Telemetriezeile. Privat: nicht im Antwortschema, das als
    # ``response_format`` an den Anbieter geht, und aus Modell-JSON nicht
    # befuellbar. Ohne das trug die Huelle eines DeepSeek-Aufrufs das Modell
    # und den Listenpreis des direkten Anbieters (02.10.2026).
    _routed_transport: str | None = PrivateAttr(default=None)
    _routed_model: str | None = PrivateAttr(default=None)
    _routed_cost_usd: float | None = PrivateAttr(default=None)

    def mark_routed(self, *, transport: str, model: str | None, cost_usd: float | None) -> None:
        self._routed_transport = transport
        self._routed_model = model or None
        self._routed_cost_usd = cost_usd

    @property
    def routed_transport(self) -> str | None:
        return self._routed_transport

    @property
    def routed_model(self) -> str | None:
        return self._routed_model

    @property
    def routed_cost_usd(self) -> float | None:
        return self._routed_cost_usd


class BaseAnalysisProvider(ABC):
    """Base interface for all LLM analysis providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider (e.g. 'openai', 'anthropic')."""

    @property
    def model(self) -> str | None:
        """Model name used by this provider instance (e.g. 'gpt-4o'). Override if applicable."""
        return None

    @abstractmethod
    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        """Analyze a document and return structured output."""
