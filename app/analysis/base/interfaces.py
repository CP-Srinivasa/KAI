from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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

    historical_analogs: list[str] = Field(default_factory=list)
    recommended_priority: int = Field(default=5, ge=1, le=10)
    actionable: bool = False
    tags: list[str] = Field(default_factory=list)


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
