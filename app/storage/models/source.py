import uuid
from datetime import UTC, datetime

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
    auth_mode: Mapped[str] = mapped_column(
        String(50), nullable=False, default=AuthMode.NONE
    )
    original_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    normalized_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_type": SourceType(self.source_type),
            "provider": self.provider,
            "status": SourceStatus(self.status),
            "auth_mode": AuthMode(self.auth_mode),
            "original_url": self.original_url,
            "normalized_url": self.normalized_url,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
