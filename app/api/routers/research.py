"""Research API router."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_document_repo
from app.core.briefs import ResearchBrief, ResearchBriefBuilder
from app.core.settings import AppSettings, get_settings
from app.core.signals import extract_signal_candidates
from app.core.watchlists import WatchlistRegistry, parse_watchlist_type
from app.storage.repositories.document_repo import DocumentRepository

router = APIRouter()


@router.get(
    "/brief",
    response_model=ResearchBrief,
    summary="Generate a research brief",
    description=(
        "Generates a structured research brief from analyzed documents "
        "filtered by a watchlist and watchlist type."
    ),
)
async def get_research_brief(
    watchlist: Annotated[
        str,
        Query(description="Named watchlist tag, for example defi"),
    ],
    watchlist_type: Annotated[
        str, Query(description="assets, persons, topics, sources")
    ] = "assets",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    repo: DocumentRepository = Depends(get_document_repo),  # noqa: B008
    settings: AppSettings = Depends(get_settings),  # noqa: B008
) -> ResearchBrief:
    watchlist_name = watchlist.strip()
    if not watchlist_name:
        raise HTTPException(status_code=400, detail="Watchlist name must not be empty.")

    monitor_dir = Path(settings.monitor_dir)
    registry = WatchlistRegistry.from_monitor_dir(monitor_dir)

    try:
        resolved_type = parse_watchlist_type(watchlist_type)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    watchlist_items = registry.get_watchlist(watchlist_name, item_type=resolved_type)

    if not watchlist_items:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Watchlist '{watchlist_name}' of type '{resolved_type}' "
                "is empty or does not exist."
            ),
        )

    documents = await repo.list(is_analyzed=True, limit=limit * 5)
    filtered_documents = registry.filter_documents(
        documents,
        watchlist_name,
        item_type=resolved_type,
    )

    builder = ResearchBriefBuilder(cluster_name=watchlist_name)
    return builder.build(filtered_documents[:limit])


@router.get(
    "/signals",
    summary="Extract signal candidates",
    description=(
        "Returns actionable signal candidates from analyzed documents. "
        "Optionally filtered by watchlist tag and minimum priority."
    ),
)
async def get_research_signals(
    watchlist: Annotated[
        str | None,
        Query(description="Watchlist tag to boost matching asset priority"),
    ] = None,
    watchlist_type: Annotated[
        str, Query(description="assets, persons, topics, sources")
    ] = "assets",
    min_priority: Annotated[int, Query(ge=1, le=10)] = 8,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    repo: DocumentRepository = Depends(get_document_repo),  # noqa: B008
    settings: AppSettings = Depends(get_settings),  # noqa: B008
) -> list[dict[str, Any]]:
    monitor_dir = Path(settings.monitor_dir)
    registry = WatchlistRegistry.from_monitor_dir(monitor_dir)

    watchlist_boosts: dict[str, int] = {}
    if watchlist:
        try:
            resolved_type = parse_watchlist_type(watchlist_type)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        symbols = registry.get_watchlist(watchlist, item_type=resolved_type)
        if symbols:
            watchlist_boosts = {s.upper(): 2 for s in symbols}

    documents = await repo.list(is_analyzed=True, limit=limit * 5)
    candidates = extract_signal_candidates(
        documents,
        min_priority=min_priority,
        watchlist_boosts=watchlist_boosts,
    )
    return [c.to_json_dict() for c in candidates[:limit]]
