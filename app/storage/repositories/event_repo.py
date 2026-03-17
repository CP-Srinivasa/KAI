"""Repository for HistoricalEvent — read-mostly, YAML-seeded."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.events import HistoricalEvent
from app.core.errors import StorageError
from app.storage.models.event import HistoricalEventModel


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, event: HistoricalEvent) -> HistoricalEvent:
        """Upsert a historical event by its id (slug)."""
        existing = await self.get_by_id(event.id)
        if existing:
            return existing
        model = _to_model(event)
        self._session.add(model)
        try:
            await self._session.flush()
        except Exception as exc:
            raise StorageError(f"Failed to save event: {exc}") from exc
        return event

    async def get_by_id(self, event_id: str) -> HistoricalEvent | None:
        result = await self._session.execute(
            select(HistoricalEventModel).where(HistoricalEventModel.id == event_id)
        )
        model = result.scalar_one_or_none()
        return _from_model(model) if model else None

    async def list_by_category(self, category: str) -> list[HistoricalEvent]:
        result = await self._session.execute(
            select(HistoricalEventModel)
            .where(HistoricalEventModel.category == category)
            .order_by(HistoricalEventModel.event_date.desc())
        )
        return [_from_model(m) for m in result.scalars().all()]

    async def list_all(self) -> list[HistoricalEvent]:
        result = await self._session.execute(
            select(HistoricalEventModel).order_by(HistoricalEventModel.event_date.desc())
        )
        return [_from_model(m) for m in result.scalars().all()]


# ── Mapping helpers ───────────────────────────────────────────────────────────


def _to_model(event: HistoricalEvent) -> HistoricalEventModel:
    return HistoricalEventModel(
        id=event.id,
        title=event.title,
        description=event.description,
        event_date=event.event_date,
        category=event.category,
        sentiment_direction=event.sentiment_direction,
        impact_magnitude=event.impact_magnitude,
        source_url=event.source_url,
        notes=event.notes,
        affected_assets=event.affected_assets,
        affected_sectors=event.affected_sectors,
        tags=event.tags,
    )


def _from_model(model: HistoricalEventModel) -> HistoricalEvent:
    from datetime import date as _date

    event_date = model.event_date
    if not isinstance(event_date, _date):
        event_date = _date.fromisoformat(str(event_date))

    return HistoricalEvent(
        id=model.id,
        title=model.title,
        description=model.description,
        event_date=event_date,
        category=model.category,
        sentiment_direction=model.sentiment_direction,  # type: ignore[arg-type]
        impact_magnitude=model.impact_magnitude,
        source_url=model.source_url,
        notes=model.notes,
        affected_assets=model.affected_assets or [],
        affected_sectors=model.affected_sectors or [],
        tags=model.tags or [],
    )
