from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SourceStatus, SourceType
from app.core.errors import StorageError
from app.ingestion.classifier import ClassificationResult, classify_url
from app.storage.repositories.source_repo import SourceRepository
from app.storage.schemas.source import SourceCreate, SourceRead, SourceUpdate

router = APIRouter(prefix="/sources", tags=["sources"])


async def _get_session() -> AsyncSession:  # pragma: no cover
    raise NotImplementedError("Session dependency must be overridden at app startup")


def _repo(session: AsyncSession = Depends(_get_session)) -> SourceRepository:  # noqa: B008
    return SourceRepository(session)


@router.post("", response_model=SourceRead, status_code=201)
async def create_source(
    data: SourceCreate,
    repo: SourceRepository = Depends(_repo),  # noqa: B008
) -> SourceRead:
    try:
        return await repo.create(data)
    except StorageError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("", response_model=list[SourceRead])
async def list_sources(
    source_type: SourceType | None = Query(None),  # noqa: B008
    status: SourceStatus | None = Query(None),  # noqa: B008
    provider: str | None = Query(None),  # noqa: B008
    repo: SourceRepository = Depends(_repo),  # noqa: B008
) -> list[SourceRead]:
    return await repo.list(source_type=source_type, status=status, provider=provider)


@router.get("/classify")
async def classify_source(
    url: str = Query(..., description="URL to classify"),  # noqa: B008
) -> dict:
    result: ClassificationResult = classify_url(url)
    return {
        "url": url,
        "source_type": result.source_type.value,
        "status": result.status.value,
        "notes": result.notes,
    }


@router.get("/{source_id}", response_model=SourceRead)
async def get_source(
    source_id: str,
    repo: SourceRepository = Depends(_repo),  # noqa: B008
) -> SourceRead:
    source = await repo.get_by_id(source_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
    return source


@router.patch("/{source_id}", response_model=SourceRead)
async def update_source(
    source_id: str,
    data: SourceUpdate,
    repo: SourceRepository = Depends(_repo),  # noqa: B008
) -> SourceRead:
    source = await repo.update(source_id, data)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
    return source


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: str,
    repo: SourceRepository = Depends(_repo),  # noqa: B008
) -> None:
    deleted = await repo.delete(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
