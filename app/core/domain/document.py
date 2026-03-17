from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.core.enums import DocumentType, SentimentLabel, SortBy, SourceType

# ── Media-type specific metadata ─────────────────────────────────────────────


class YouTubeVideoMeta(BaseModel):
    """Metadata specific to YouTube videos."""

    video_id: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    thumbnail_url: str | None = None


class PodcastEpisodeMeta(BaseModel):
    """Metadata specific to podcast episodes."""

    podcast_title: str | None = None
    episode_number: int | None = None
    season: int | None = None
    audio_url: str | None = None
    duration_seconds: int | None = None
    feed_url: str | None = None


# ── Unified document model ────────────────────────────────────────────────────


class CanonicalDocument(BaseModel):
    """Unified document representation for all source types.

    Used for news articles, podcast episodes, YouTube videos, and web pages.
    Media-specific details live in youtube_meta / podcast_meta sub-models.
    All other analysis fields (sentiment, scores, tags) are shared.
    """

    id: UUID = Field(default_factory=uuid4)
    external_id: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    source_type: SourceType | None = None
    document_type: DocumentType = DocumentType.UNKNOWN
    provider: str | None = None

    url: str
    title: str
    subtitle: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    language: str | None = None
    country: str | None = None
    region: str | None = None
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    raw_text: str | None = None
    cleaned_text: str | None = None
    summary: str | None = None

    # Entity extraction
    entities: list[str] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    crypto_assets: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)

    # Analysis scores
    sentiment_label: SentimentLabel | None = None
    sentiment_score: float | None = None
    relevance_score: float | None = None
    impact_score: float | None = None
    credibility_score: float | None = None
    novelty_score: float | None = None
    historical_similarity_score: float | None = None

    # Engagement signals
    clicks: int | None = None
    views: int | None = None
    engagement: int | None = None

    # AI-enriched fields
    ai_tags: list[str] = Field(default_factory=list)
    ai_region: str | None = None
    ai_organizations: list[str] = Field(default_factory=list)
    related_events: list[str] = Field(default_factory=list)

    # Media-type specific metadata (only one populated per document)
    youtube_meta: YouTubeVideoMeta | None = None
    podcast_meta: PodcastEpisodeMeta | None = None

    # Catch-all for source-specific extras
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None

    def compute_hash(self) -> str:
        content = f"{self.url}|{self.title}|{self.raw_text or ''}"
        return hashlib.sha256(content.encode()).hexdigest()


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

    min_credibility: float | None = None
    min_sentiment_abs: float | None = None
    min_views: int | None = None
    min_clicks: int | None = None

    deduplicate: bool = True
    sort_by: SortBy = SortBy.PUBLISHED_AT
    limit: int = Field(default=50, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
