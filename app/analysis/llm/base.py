"""
LLM Analysis Provider - Base Interface
=======================================
Provider-agnostic interface for all LLM analysis operations.
All implementations produce structured, validated outputs.
Prompts versioned in config/prompts/.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import DocumentPriority, EventType, MarketScope, SentimentLabel
from app.core.errors import LLMCostLimitError
from app.core.logging import get_logger

logger = get_logger(__name__)

ANALYSIS_PROMPT_VERSION = "v1.0"

DOCUMENT_ANALYSIS_SYSTEM_PROMPT = """You are a senior financial analyst and crypto market expert.
Analyze the provided document and return a structured JSON assessment.
Be precise, objective, and grounded in the actual content.
Do not speculate beyond what the document states.
Return ONLY valid JSON matching the provided schema."""

DOCUMENT_ANALYSIS_USER_TEMPLATE = """Analyze the following document:

TITLE: {title}
SOURCE: {source_name} ({source_type})
PUBLISHED: {published_at}
CONTENT:
{content}

Return a JSON object with this exact structure:
{json_schema}"""


class LLMAnalysisOutput(BaseModel):
    """Validated output schema for LLM document analysis."""
    sentiment_label: SentimentLabel
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    relevance_score: float = Field(ge=0.0, le=1.0)
    impact_score: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    novelty_score: float = Field(ge=0.0, le=1.0)
    spam_probability: float = Field(ge=0.0, le=1.0)
    market_scope: MarketScope
    affected_assets: list[str]
    affected_sectors: list[str]
    event_type: EventType
    bull_case: str
    bear_case: str
    neutral_case: str
    historical_analogs: list[str]
    recommended_priority: DocumentPriority
    actionable: bool
    tags: list[str]
    explanation_short: str
    explanation_long: str

    @field_validator("sentiment_score")
    @classmethod
    def round_sentiment(cls, v: float) -> float:
        return round(v, 4)

    @field_validator("explanation_short")
    @classmethod
    def non_empty_short(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("explanation_short must not be empty")
        return v.strip()


DOCUMENT_ANALYSIS_JSON_SCHEMA = {
    "type": "object",
    "required": [
        "sentiment_label", "sentiment_score", "relevance_score", "impact_score",
        "confidence_score", "novelty_score", "spam_probability", "market_scope",
        "affected_assets", "affected_sectors", "event_type", "bull_case",
        "bear_case", "neutral_case", "historical_analogs", "recommended_priority",
        "actionable", "tags", "explanation_short", "explanation_long"
    ],
    "properties": {
        "sentiment_label": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "sentiment_score": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "relevance_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "impact_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "confidence_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "novelty_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "spam_probability": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "market_scope": {"type": "string", "enum": ["crypto", "equities", "macro", "mixed", "unknown"]},
        "affected_assets": {"type": "array", "items": {"type": "string"}},
        "affected_sectors": {"type": "array", "items": {"type": "string"}},
        "event_type": {"type": "string", "enum": [
            "regulatory", "earnings", "macro_economic", "technical", "social_sentiment",
            "hack_exploit", "partnership", "listing_delisting", "fork_upgrade", "legal",
            "merger_acquisition", "product_launch", "market_manipulation",
            "whale_movement", "other", "unknown"
        ]},
        "bull_case": {"type": "string"},
        "bear_case": {"type": "string"},
        "neutral_case": {"type": "string"},
        "historical_analogs": {"type": "array", "items": {"type": "string"}},
        "recommended_priority": {"type": "string", "enum": ["critical", "high", "medium", "low", "noise"]},
        "actionable": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "explanation_short": {"type": "string", "minLength": 10},
        "explanation_long": {"type": "string"},
    },
}


class UsageRecord(BaseModel):
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    document_id: str = ""
    operation: str = "analyze_document"
    success: bool = True
    error: str | None = None


class BaseAnalysisProvider(ABC):
    """Abstract base for all LLM providers. Handles cost limits and observability."""

    def __init__(
        self,
        provider_name: str,
        model: str,
        cost_limit_usd_per_day: float = 10.0,
        max_retries: int = 3,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.provider_name = provider_name
        self.model = model
        self.cost_limit_usd_per_day = cost_limit_usd_per_day
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self._daily_cost_usd: float = 0.0
        self._daily_reset_date: str = datetime.utcnow().strftime("%Y-%m-%d")
        self._usage_history: list[UsageRecord] = []

    def _check_cost_limit(self) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_cost_usd = 0.0
            self._daily_reset_date = today
        if self._daily_cost_usd >= self.cost_limit_usd_per_day:
            raise LLMCostLimitError(
                f"Daily cost limit ${self.cost_limit_usd_per_day:.2f} exceeded "
                f"(current: ${self._daily_cost_usd:.4f})"
            )

    def _record_usage(self, usage: UsageRecord) -> None:
        self._daily_cost_usd += usage.cost_usd
        self._usage_history.append(usage)
        logger.info(
            "llm_usage",
            provider=usage.provider, model=usage.model,
            tokens=usage.total_tokens, cost_usd=usage.cost_usd,
            latency_ms=usage.latency_ms, success=usage.success,
        )

    @abstractmethod
    async def analyze_document(self, document: CanonicalDocument) -> AnalysisResult: ...

    @abstractmethod
    async def summarize_document(self, document: CanonicalDocument, max_words: int = 100) -> str: ...

    @abstractmethod
    async def compare_to_historical(
        self, document: CanonicalDocument, historical_events: list[str]
    ) -> list[str]: ...

    @abstractmethod
    async def healthcheck(self) -> dict[str, Any]: ...

    def get_daily_cost(self) -> float:
        return self._daily_cost_usd

    def get_usage_stats(self) -> dict[str, Any]:
        total = len(self._usage_history)
        ok = sum(1 for u in self._usage_history if u.success)
        return {
            "provider": self.provider_name, "model": self.model,
            "total_calls": total, "successful_calls": ok, "failed_calls": total - ok,
            "daily_cost_usd": round(self._daily_cost_usd, 6),
            "daily_limit_usd": self.cost_limit_usd_per_day,
        }
