"""Sources management endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.enums import SourceStatus, SourceType
from app.ingestion.source_registry import get_registry

router = APIRouter()


@router.get("/")
async def list_sources(
    status: str | None = Query(None, description="Filter by status (active, requires_api, disabled, ...)"),
    source_type: str | None = Query(None, alias="type", description="Filter by source type (rss_feed, website, ...)"),
    fetchable_only: bool = Query(False, description="Return only fetchable sources"),
) -> dict[str, Any]:
    """
    List all registered sources from the in-memory SourceRegistry.

    The registry is populated at startup from monitor/ files.
    Use ?fetchable_only=true to get only sources that can be actively ingested.
    """
    registry = get_registry()

    if fetchable_only:
        entries = registry.fetchable()
    else:
        entries = registry.all()

    # Apply optional filters
    if status:
        try:
            status_enum = SourceStatus(status)
            entries = [e for e in entries if e.status == status_enum]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid values: {[s.value for s in SourceStatus]}",
            )

    if source_type:
        try:
            type_enum = SourceType(source_type)
            entries = [e for e in entries if e.source_type == type_enum]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid type '{source_type}'. Valid values: {[t.value for t in SourceType]}",
            )

    return {
        "sources": [e.to_dict() for e in entries],
        "total": len(entries),
        "registry_summary": registry.summary(),
    }


@router.get("/summary")
async def registry_summary() -> dict[str, Any]:
    """Return a summary of the source registry (counts by status and type)."""
    registry = get_registry()
    return registry.summary()


@router.get("/{source_id}")
async def get_source(source_id: str) -> dict[str, Any]:
    """Get a single source by ID from the registry."""
    registry = get_registry()
    entry = registry.get(source_id)

    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Source '{source_id}' not found in registry",
        )

    return entry.to_dict()
