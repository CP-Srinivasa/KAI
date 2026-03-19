"""ORM model for historical_events table."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Date, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.db.session import Base


class HistoricalEventModel(Base):
    __tablename__ = "historical_events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # slug
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    event_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    sentiment_direction: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="neutral"
    )
    impact_magnitude: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON columns
    affected_assets: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    affected_sectors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
