"""Historical event domain models.

HistoricalEvent: a notable past market event used as reference point.
EventAnalog: a detected similarity between a current document and a past event.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class HistoricalEvent(BaseModel):
    """A notable historical market event stored as reference data."""

    id: str  # slug, e.g. "btc-halving-2024"
    title: str
    description: str
    event_date: date
    category: str  # "halving" | "crash" | "regulatory" | "hack" | "etf" | "macro" | "other"
    affected_assets: list[str] = Field(default_factory=list)
    affected_sectors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sentiment_direction: Literal["bullish", "bearish", "neutral", "mixed"] = "neutral"
    # Approximate impact on price/market, 0.0–1.0
    impact_magnitude: float = Field(default=0.5, ge=0.0, le=1.0)
    source_url: str | None = None
    notes: str | None = None


class EventAnalog(BaseModel):
    """A detected analog between a current document and a historical event."""

    event_id: str  # HistoricalEvent.id
    event_title: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    matching_reason: str  # human-readable explanation
    # Which assets from the event also appear in the document
    shared_assets: list[str] = Field(default_factory=list)
    shared_tags: list[str] = Field(default_factory=list)
