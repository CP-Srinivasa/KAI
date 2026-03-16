"""SQLAlchemy ORM Models — SQLAlchemy 2.x with async support."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    auth_mode: Mapped[str] = mapped_column(String(50), default="none")
    status: Mapped[str] = mapped_column(String(50), default="active")
    language: Mapped[str] = mapped_column(String(10), default="en")
    country: Mapped[str] = mapped_column(String(10), default="")
    categories: Mapped[list[Any]] = mapped_column(JSON, default=list)
    credibility_score: Mapped[float] = mapped_column(Float, default=0.5)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    raw_documents: Mapped[list[RawDocument]] = relationship(back_populates="source")
    canonical_documents: Mapped[list[CanonicalDocumentDB]] = relationship(back_populates="source")


class RawDocument(Base):
    __tablename__ = "raw_documents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("source_id", "content_hash", name="uq_raw_source_hash"),)
    source: Mapped[Source] = relationship(back_populates="raw_documents")


class CanonicalDocumentDB(Base):
    __tablename__ = "canonical_documents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(500), default="")
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), default="")
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), default="")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, default="")
    subtitle: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(255), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    language: Mapped[str] = mapped_column(String(10), default="unknown")
    country: Mapped[str] = mapped_column(String(10), default="")
    region: Mapped[str] = mapped_column(String(100), default="")
    categories: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tickers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    crypto_assets: Mapped[list[Any]] = mapped_column(JSON, default=list)
    people: Mapped[list[Any]] = mapped_column(JSON, default=list)
    organizations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    topics: Mapped[list[Any]] = mapped_column(JSON, default=list)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    cleaned_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    engagement: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    canonical_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(50), default="pending")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("content_hash", name="uq_canonical_content_hash"),)
    source: Mapped[Source] = relationship(back_populates="canonical_documents")
    analysis: Mapped[DocumentAnalysisDB | None] = relationship(back_populates="document", uselist=False)


class DocumentAnalysisDB(Base):
    __tablename__ = "document_analysis"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_documents.id"), nullable=False, unique=True)
    sentiment_label: Mapped[str] = mapped_column(String(20), default="neutral")
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    novelty_score: Mapped[float] = mapped_column(Float, default=0.0)
    credibility_score: Mapped[float] = mapped_column(Float, default=0.5)
    spam_probability: Mapped[float] = mapped_column(Float, default=0.0)
    market_scope: Mapped[str] = mapped_column(String(50), default="unknown")
    event_type: Mapped[str] = mapped_column(String(100), default="unknown")
    recommended_priority: Mapped[str] = mapped_column(String(20), default="low")
    actionable: Mapped[bool] = mapped_column(Boolean, default=False)
    affected_assets: Mapped[list[Any]] = mapped_column(JSON, default=list)
    affected_sectors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    bull_case: Mapped[str] = mapped_column(Text, default="")
    bear_case: Mapped[str] = mapped_column(Text, default="")
    neutral_case: Mapped[str] = mapped_column(Text, default="")
    historical_analogs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    explanation_short: Mapped[str] = mapped_column(Text, default="")
    explanation_long: Mapped[str] = mapped_column(Text, default="")
    analyzed_by: Mapped[str] = mapped_column(String(100), default="")
    analysis_model: Mapped[str] = mapped_column(String(100), default="")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    document: Mapped[CanonicalDocumentDB] = relationship(back_populates="analysis")


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(Text, default="")
    message: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
