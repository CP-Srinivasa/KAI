import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.storage.db.session import Base


class SourceModel(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=SourceStatus.PLANNED, index=True
    )
    auth_mode: Mapped[str] = mapped_column(String(50), nullable=False, default=AuthMode.NONE)
    original_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    normalized_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Source-lifecycle columns (Phase 3 foundation, PR5a). All nullable + additive:
    # dormant until the discovery/graduation engine (later Phase-3 PR) writes them.
    # ``last_activity_at`` is signal-level (last directional dispatch), not raw
    # ingestion; ``lifecycle_tier`` mirrors monitor/source_ranking.json.
    probation_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifecycle_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": SourceType(self.source_type),
            "provider": self.provider,
            "status": SourceStatus(self.status),
            "auth_mode": AuthMode(self.auth_mode),
            "original_url": self.original_url,
            "normalized_url": self.normalized_url,
            "notes": self.notes,
            "probation_start_at": self.probation_start_at,
            "last_activity_at": self.last_activity_at,
            "pinned_at": self.pinned_at,
            "lifecycle_tier": self.lifecycle_tier,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
