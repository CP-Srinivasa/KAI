"""ORM model for canonical_documents table.

Key queryable fields are stored as columns.
All structured extras (entity_mentions, scores, meta) are stored as JSON.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.db.session import Base


class CanonicalDocumentModel(Base):
    __tablename__ = "canonical_documents"
    __table_args__ = (
        Index("ix_canonical_documents_content_hash", "content_hash", unique=True),
        Index("ix_canonical_documents_source_published", "source_id", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    document_type: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="unknown", index=True
    )
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)

    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    market_scope: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="unknown"
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleaned_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Analysis scores
    sentiment_label: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    impact_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    credibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # State flags
    is_duplicate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", index=True
    )
    is_analyzed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", index=True
    )

    # JSON columns — structured extras
    entity_mentions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    entities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tickers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    crypto_assets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    people: Mapped[list | None] = mapped_column(JSON, nullable=True)
    organizations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    topics: Mapped[list | None] = mapped_column(JSON, nullable=True)
    categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    youtube_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    podcast_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
