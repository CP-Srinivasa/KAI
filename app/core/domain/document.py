"""Canonical Document Model.

Single unified representation for all source types:
  - News articles (RSS, news API, web scrape)
  - Podcast episodes
  - YouTube videos
  - Reference pages / blog posts

Media-specific detail lives in YouTubeVideoMeta / PodcastEpisodeMeta.
LLM analysis output lives in AnalysisResult (separate, links by document_id).
Entity extraction lives in EntityMention (structured, typed).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

from app.core.enums import DocumentType, MarketScope, SentimentLabel, SortBy, SourceType

# ── Entity Mention ────────────────────────────────────────────────────────────


class EntityMention(BaseModel):
    """A named entity extracted from a document.

    entity_type: person | organization | asset | crypto_asset | location | event | topic
    source: rule | llm | manual
    """

    name: str
    entity_type: str
    normalized_name: str | None = None
    context: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "rule"
    url: str | None = None


# ── Media-type specific metadata ─────────────────────────────────────────────


class YouTubeVideoMeta(BaseModel):
    video_id: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    thumbnail_url: str | None = None


class PodcastEpisodeMeta(BaseModel):
    podcast_title: str | None = None
    episode_number: int | None = None
    season: int | None = None
    audio_url: str | None = None
    duration_seconds: int | None = None
    feed_url: str | None = None


# ── Canonical Document ────────────────────────────────────────────────────────


class CanonicalDocument(BaseModel):
    """Unified document representation for all source types.

    Rules:
    - url is always present and the primary dedup key
    - content_hash is auto-computed from url + title + raw_text on creation
    - media-specific details live in youtube_meta / podcast_meta
    - LLM analysis results stored separately (AnalysisResult), linked by id
    - word_count is a computed property, never stored
    """

    id: UUID = Field(default_factory=uuid4)
    external_id: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    source_type: SourceType | None = None
    document_type: DocumentType = DocumentType.UNKNOWN
    provider: str | None = None

    # Core content
    url: str
    title: str
    subtitle: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    language: str | None = None
    country: str | None = None
    region: str | None = None
    market_scope: MarketScope = MarketScope.UNKNOWN

    # Taxonomy
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)

    # Text content
    raw_text: str | None = None
    cleaned_text: str | None = None
    summary: str | None = None

    # Structured entity extraction
    entity_mentions: list[EntityMention] = Field(default_factory=list)

    # Flat entity lists (quick access, synced from entity_mentions or set directly)
    entities: list[str] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    crypto_assets: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)

    # Analysis scores (set after analysis pipeline runs)
    sentiment_label: SentimentLabel | None = None
    sentiment_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    impact_score: float | None = Field(default=None, ge=0.0, le=1.0)
    credibility_score: float | None = Field(default=None, ge=0.0, le=1.0)
    novelty_score: float | None = Field(default=None, ge=0.0, le=1.0)
    historical_similarity_score: float | None = Field(default=None, ge=0.0, le=1.0)

    # Engagement signals
    clicks: int | None = None
    views: int | None = None
    engagement: int | None = None

    # AI-enriched extras
    ai_tags: list[str] = Field(default_factory=list)
    ai_region: str | None = None
    ai_organizations: list[str] = Field(default_factory=list)
    related_events: list[str] = Field(default_factory=list)

    # State flags
    is_duplicate: bool = False
    is_analyzed: bool = False

    # Media-type specific metadata
    youtube_meta: YouTubeVideoMeta | None = None
    podcast_meta: PodcastEpisodeMeta | None = None

    # Catch-all for source-specific extras
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Auto-computed on creation — do not set manually
    content_hash: str | None = None

    @model_validator(mode="after")
    def _auto_hash(self) -> CanonicalDocument:
        if not self.content_hash:
            self.content_hash = self._compute_hash()
        return self

    def _compute_hash(self) -> str:
        content = f"{self.url}|{self.title}|{self.raw_text or ''}"
        return hashlib.sha256(content.encode()).hexdigest()

    @computed_field  # type: ignore[misc]
    @property
    def word_count(self) -> int:
        text = self.cleaned_text or self.raw_text or ""
        return len(text.split()) if text else 0


# ── Analysis Result ───────────────────────────────────────────────────────────


class AnalysisResult(BaseModel):
    """Output of one LLM or rule-based analysis run on a CanonicalDocument.

    One document can have multiple AnalysisResults (one per provider/run).
    Always links back to CanonicalDocument via document_id.
    raw_output preserves the original LLM response for debugging and re-analysis.
    """

    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    provider: str  # "openai" | "anthropic" | "rule"
    model: str | None = None  # e.g. "gpt-4o"
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Core scores — all validated ranges
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

    # Reasoning (structured LLM output)
    short_reasoning: str | None = None
    long_reasoning: str | None = None
    bull_case: str | None = None
    bear_case: str | None = None
    neutral_case: str | None = None

    historical_analogs: list[str] = Field(default_factory=list)
    recommended_priority: int = Field(default=5, ge=1, le=10)
    actionable: bool = False
    tags: list[str] = Field(default_factory=list)

    # Preserved for debugging / re-analysis
    raw_output: dict[str, Any] = Field(default_factory=dict)


# ── Query DSL ─────────────────────────────────────────────────────────────────


class QuerySpec(BaseModel):
    query_text: str | None = None
    include_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    any_terms: list[str] = Field(default_factory=list)
    all_terms: list[str] = Field(default_factory=list)
    exact_phrases: list[str] = Field(default_factory=list)
    title_terms: list[str] = Field(default_factory=list)
    meta_terms: list[str] = Field(default_factory=list)

    from_date: datetime | None = None
    to_date: datetime | None = None
    countries: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    source_types: list[SourceType] = Field(default_factory=list)
    document_types: list[DocumentType] = Field(default_factory=list)
    market_scopes: list[MarketScope] = Field(default_factory=list)

    min_credibility: float | None = None
    min_sentiment_abs: float | None = None
    min_views: int | None = None
    min_clicks: int | None = None
    only_actionable: bool = False
    exclude_duplicates: bool = True

    sort_by: SortBy = SortBy.PUBLISHED_AT
    limit: int = Field(default=50, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
