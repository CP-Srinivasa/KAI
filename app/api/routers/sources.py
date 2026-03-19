from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_source_repo
from app.core.enums import SourceStatus, SourceType
from app.core.errors import StorageError
from app.core.settings import get_settings
from app.ingestion.classifier import ClassificationResult, SourceClassifier
from app.storage.repositories.source_repo import SourceRepository
from app.storage.schemas.source import SourceCreate, SourceRead, SourceUpdate

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post("", response_model=SourceRead, status_code=201)
async def create_source(
    data: SourceCreate,
    repo: SourceRepository = Depends(get_source_repo),  # noqa: B008
) -> SourceRead:
    try:
        return await repo.create(data)
    except StorageError as e:
        # Do not expose internal DB error details to the caller
        raise HTTPException(
            status_code=409, detail="Source already exists or constraint violated"
        ) from e


@router.get("", response_model=list[SourceRead])
async def list_sources(
    source_type: SourceType | None = Query(None),  # noqa: B008
    status: SourceStatus | None = Query(None),  # noqa: B008
    provider: str | None = Query(None),  # noqa: B008
    repo: SourceRepository = Depends(get_source_repo),  # noqa: B008
) -> list[SourceRead]:
    return await repo.list(source_type=source_type, status=status, provider=provider)


@router.get("/classify")
async def classify_source(
    url: str = Query(..., description="URL to classify"),  # noqa: B008
) -> dict[str, Any]:
    settings = get_settings()
    classifier = SourceClassifier.from_monitor_dir(Path(settings.monitor_dir))
    result: ClassificationResult = classifier.classify(url)
    return {
        "url": url,
        "source_type": result.source_type.value,
        "status": result.status.value,
        "notes": result.notes,
    }


@router.get("/{source_id}", response_model=SourceRead)
async def get_source(
    source_id: str,
    repo: SourceRepository = Depends(get_source_repo),  # noqa: B008
) -> SourceRead:
    source = await repo.get_by_id(source_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
    return source


@router.patch("/{source_id}", response_model=SourceRead)
async def update_source(
    source_id: str,
    data: SourceUpdate,
    repo: SourceRepository = Depends(get_source_repo),  # noqa: B008
) -> SourceRead:
    source = await repo.update(source_id, data)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
    return source


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: str,
    repo: SourceRepository = Depends(get_source_repo),  # noqa: B008
) -> None:
    deleted = await repo.delete(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
