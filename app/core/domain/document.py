"""
Canonical Document Domain Model
================================
The central data model. Every source adapter normalizes to CanonicalDocument.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

from app.core.enums import (
    AnalysisStatus,
    DocumentPriority,
    EventType,
    Language,
    MarketScope,
    SentimentLabel,
    SourceType,
)
from app.core.types import ContentHash, DocumentId, JsonDict, ScoreFloat, SentimentScore


class EntityMention(BaseModel):
    name: str
    entity_type: str  # person, organization, ticker, crypto_asset, location
    canonical_name: str | None = None
    confidence: float = 1.0
    mentions: int = 1


class AnalysisResult(BaseModel):
    """Structured LLM analysis result. Validated before storage."""
    sentiment_label: SentimentLabel = SentimentLabel.NEUTRAL
    sentiment_score: SentimentScore = 0.0
    relevance_score: ScoreFloat = 0.0
    impact_score: ScoreFloat = 0.0
    confidence_score: ScoreFloat = 0.0
    novelty_score: ScoreFloat = 0.0
    credibility_score: ScoreFloat = 0.5
    spam_probability: ScoreFloat = 0.0
    market_scope: MarketScope = MarketScope.UNKNOWN
    affected_assets: list[str] = Field(default_factory=list)
    affected_sectors: list[str] = Field(default_factory=list)
    event_type: EventType = EventType.UNKNOWN
    bull_case: str = ""
    bear_case: str = ""
    neutral_case: str = ""
    historical_analogs: list[str] = Field(default_factory=list)
    narrative_cluster: str | None = None
    recommended_priority: DocumentPriority = DocumentPriority.LOW
    actionable: bool = False
    tags: list[str] = Field(default_factory=list)
    explanation_short: str = ""
    explanation_long: str = ""
    analyzed_by: str = ""
    analyzed_at: datetime | None = None
    analysis_model: str = ""
    token_count: int = 0
    cost_usd: float = 0.0


class CanonicalDocument(BaseModel):
    """Unified document schema. All adapters produce this after normalization."""
    id: DocumentId = Field(default_factory=uuid4)
    external_id: str = ""
    source_id: str
    source_name: str
    source_type: SourceType
    provider: str = ""
    url: str
    title: str
    subtitle: str = ""
    author: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    language: Language = Language.UNKNOWN
    country: str = ""
    region: str = ""
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    raw_text: str = ""
    cleaned_text: str = ""
    summary: str = ""
    entities: list[EntityMention] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    crypto_assets: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    clicks: int = 0
    views: int = 0
    engagement: int = 0
    analysis: AnalysisResult | None = None
    analysis_status: AnalysisStatus = AnalysisStatus.PENDING
    content_hash: ContentHash = ""
    is_duplicate: bool = False
    canonical_id: DocumentId | None = None
    related_events: list[str] = Field(default_factory=list)
    ai_tags: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def set_content_hash(self) -> CanonicalDocument:
        if not self.content_hash:
            payload = f"{self.url}|{self.title}|{self.published_at}"
            self.content_hash = hashlib.sha256(payload.encode()).hexdigest()
        return self

    @property
    def is_analyzed(self) -> bool:
        return self.analysis_status == AnalysisStatus.COMPLETED

    @property
    def impact_score(self) -> float:
        return self.analysis.impact_score if self.analysis else 0.0
