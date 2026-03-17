from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.ingestion.classifier import ClassificationResult, classify_url

router = APIRouter(prefix="/sources", tags=["sources"])


class SourcesResponse(BaseModel):
    message: str


class ClassifyResponse(BaseModel):
    url: str
    source_type: str
    status: str
    notes: str | None


@router.get("", response_model=SourcesResponse)
async def list_sources() -> SourcesResponse:
    return SourcesResponse(message="Sources endpoint — Phase 2 (storage integration in Phase 2+)")


@router.get("/classify", response_model=ClassifyResponse)
async def classify_source(
    url: str = Query(..., description="URL to classify"),
) -> ClassifyResponse:
    result: ClassificationResult = classify_url(url)
    return ClassifyResponse(
        url=url,
        source_type=result.source_type.value,
        status=result.status.value,
        notes=result.notes,
    )
