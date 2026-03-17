import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SourceStatus, SourceType
from app.core.errors import StorageError
from app.storage.models.source import SourceModel
from app.storage.schemas.source import SourceCreate, SourceRead, SourceUpdate


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: SourceCreate) -> SourceRead:
        model = SourceModel(
            source_id=str(uuid.uuid4()),
            source_type=data.source_type.value,
            provider=data.provider,
            status=data.status.value,
            auth_mode=data.auth_mode.value,
            original_url=data.original_url,
            normalized_url=data.normalized_url,
            notes=data.notes,
        )
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except Exception as e:
            raise StorageError(f"Failed to create source: {e}") from e
        return SourceRead.model_validate(model)

    async def get_by_id(self, source_id: str) -> SourceRead | None:
        result = await self._session.execute(
            select(SourceModel).where(SourceModel.source_id == source_id)
        )
        model = result.scalar_one_or_none()
        return SourceRead.model_validate(model) if model else None

    async def get_by_url(self, original_url: str) -> SourceRead | None:
        result = await self._session.execute(
            select(SourceModel).where(SourceModel.original_url == original_url.strip())
        )
        model = result.scalar_one_or_none()
        return SourceRead.model_validate(model) if model else None

    async def list(
        self,
        source_type: SourceType | None = None,
        status: SourceStatus | None = None,
        provider: str | None = None,
    ) -> list[SourceRead]:
        stmt = select(SourceModel)
        if source_type:
            stmt = stmt.where(SourceModel.source_type == source_type.value)
        if status:
            stmt = stmt.where(SourceModel.status == status.value)
        if provider:
            stmt = stmt.where(SourceModel.provider == provider)
        stmt = stmt.order_by(SourceModel.created_at.desc())
        result = await self._session.execute(stmt)
        return [SourceRead.model_validate(m) for m in result.scalars().all()]

    async def update(self, source_id: str, data: SourceUpdate) -> SourceRead | None:
        changes = dict(data.model_dump(exclude_none=True))
        if not changes:
            return await self.get_by_id(source_id)
        for key in ("source_type", "status", "auth_mode"):
            if key in changes and hasattr(changes[key], "value"):
                changes[key] = changes[key].value
        changes["updated_at"] = datetime.now(UTC)
        await self._session.execute(
            update(SourceModel)
            .where(SourceModel.source_id == source_id)
            .values(**changes)
        )
        await self._session.flush()
        return await self.get_by_id(source_id)

    async def delete(self, source_id: str) -> bool:
        model = await self._session.get(SourceModel, source_id)
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True
