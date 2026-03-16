"""
Source Repository
=================
CRUD operations for Source records.
All methods are async and use SQLAlchemy 2.x style.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SourceStatus, SourceType
from app.core.logging import get_logger
from app.storage.models.db_models import Source

logger = get_logger(__name__)


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, source_id: str) -> Source | None:
        result = await self._session.execute(select(Source).where(Source.id == source_id))
        return result.scalar_one_or_none()

    async def list_active(self) -> list[Source]:
        result = await self._session.execute(
            select(Source).where(Source.status == SourceStatus.ACTIVE.value)
        )
        return list(result.scalars().all())

    async def list_by_type(self, source_type: SourceType) -> list[Source]:
        result = await self._session.execute(
            select(Source).where(Source.source_type == source_type.value)
        )
        return list(result.scalars().all())

    async def upsert(self, source: Source) -> Source:
        """Insert or update a source record."""
        existing = await self.get_by_id(source.id)
        if existing:
            await self._session.execute(
                update(Source)
                .where(Source.id == source.id)
                .values(
                    name=source.name,
                    status=source.status,
                    last_fetched_at=source.last_fetched_at,
                    last_error=source.last_error,
                    consecutive_errors=source.consecutive_errors,
                    updated_at=source.updated_at,
                )
            )
            logger.debug("source_updated", source_id=source.id)
        else:
            self._session.add(source)
            logger.info("source_created", source_id=source.id, type=source.source_type)
        return source

    async def mark_error(self, source_id: str, error: str) -> None:
        await self._session.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(
                last_error=error,
                consecutive_errors=Source.consecutive_errors + 1,
            )
        )

    async def mark_fetched(self, source_id: str) -> None:
        from datetime import datetime
        await self._session.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(
                last_fetched_at=datetime.utcnow(),
                last_error=None,
                consecutive_errors=0,
            )
        )
