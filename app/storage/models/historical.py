"""
Historical Event Models
=======================
Domain (Pydantic) and ORM (SQLAlchemy) models for historical market events.

These are the foundation for:
- Comparative analysis ("this resembles X from 2020")
- Event outcome tracking
- Similarity candidate ranking

Not a full prediction engine — this is the data structure layer.
Future phases can build training data and similarity scoring on top.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EventType, MarketScope, SentimentLabel
from app.storage.models.db_models import Base


# ─────────────────────────────────────────────
# Pydantic Domain Models
# ─────────────────────────────────────────────

class HistoricalEvent(BaseModel):
    """
    A notable past market event stored for comparative analysis.

    Examples:
    - Luna/UST depeg (May 2022)
    - FTX collapse (Nov 2022)
    - Bitcoin ETF approval (Jan 2024)
    - Fed rate hike surprise
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str = ""
    event_type: EventType = EventType.UNKNOWN
    market_scope: MarketScope = MarketScope.UNKNOWN
    sentiment_label: SentimentLabel = SentimentLabel.NEUTRAL
    occurred_at: datetime
    source_url: str = ""
    affected_assets: list[str] = Field(default_factory=list)
    affected_sectors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    outcome_summary: str = ""
    max_price_impact_pct: float | None = None   # e.g. -80.0 for -80%
    resolution_days: int | None = None           # How long the event played out
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        use_enum_values = True


class EventOutcome(BaseModel):
    """
    Recorded outcome of a historical event.
    Used for training similarity scoring and outcome prediction.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str
    asset: str
    price_before: float | None = None
    price_after: float | None = None
    price_change_pct: float | None = None
    time_to_bottom_days: int | None = None
    time_to_recovery_days: int | None = None
    recovery_achieved: bool = False
    notes: str = ""
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


class EventSimilarityCandidate(BaseModel):
    """
    A candidate pair: current document potentially similar to a historical event.
    Created by the analysis pipeline as input for LLM comparison.
    """
    document_id: str
    historical_event_id: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    similarity_reason: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────
# SQLAlchemy ORM Models
# ─────────────────────────────────────────────

class HistoricalEventDB(Base):
    __tablename__ = "historical_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    event_type: Mapped[str] = mapped_column(String(100), default="unknown")
    market_scope: Mapped[str] = mapped_column(String(50), default="unknown")
    sentiment_label: Mapped[str] = mapped_column(String(20), default="neutral")
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, default="")
    affected_assets: Mapped[list[Any]] = mapped_column(JSON, default=list)
    affected_sectors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    outcome_summary: Mapped[str] = mapped_column(Text, default="")
    max_price_impact_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventOutcomeDB(Base):
    __tablename__ = "event_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset: Mapped[str] = mapped_column(String(100), nullable=False)
    price_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_bottom_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_to_recovery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recovery_achieved: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
