"""
Watchlist Endpoints
====================
GET  /watchlists/              — list all watchlist categories + item counts
GET  /watchlists/{category}    — list all items in a category
GET  /watchlists/search        — find items matching a text query
POST /watchlists/sync          — reload watchlists.yml from disk
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_WATCHLIST_FILE = Path("monitor/watchlists.yml")


def _get_registry():
    from app.trading.watchlists.watchlist import WatchlistRegistry
    return WatchlistRegistry.from_file(_WATCHLIST_FILE)


@router.get("/")
async def list_watchlists() -> dict[str, Any]:
    """List all watchlist categories with item counts."""
    registry = _get_registry()
    return {
        "summary": registry.summary(),
        "total": registry.total,
        "file": str(_WATCHLIST_FILE),
    }


@router.get("/search")
async def search_watchlists(
    q: str = Query(..., description="Text to search for watchlist hits"),
) -> dict[str, Any]:
    """Find all watchlist items matching the query text."""
    registry = _get_registry()
    matches = registry.find_by_text(q)
    return {
        "query": q,
        "matches": [
            {
                "category": m.item.category.value,
                "identifier": m.item.identifier,
                "display_name": m.item.display_name,
                "matched_alias": m.matched_alias,
                "tags": m.item.tags,
            }
            for m in matches
        ],
        "total": len(matches),
    }


@router.get("/{category}")
async def list_category(
    category: str,
    tag: str | None = Query(None, description="Filter by tag"),
) -> dict[str, Any]:
    """List all items in a watchlist category."""
    from app.core.enums import WatchlistCategory

    try:
        cat = WatchlistCategory(category.lower())
    except ValueError:
        valid = [c.value for c in WatchlistCategory]
        raise HTTPException(
            status_code=400,
            detail=f"Unknown category '{category}'. Valid: {valid}",
        )

    registry = _get_registry()
    items = registry.get_by_category(cat)

    if tag:
        items = [i for i in items if tag.lower() in [t.lower() for t in i.tags]]

    return {
        "category": category,
        "items": [i.to_dict() for i in items],
        "total": len(items),
    }


@router.post("/sync")
async def sync_watchlists() -> dict[str, Any]:
    """Reload watchlists from disk (e.g. after editing watchlists.yml)."""
    if not _WATCHLIST_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Watchlist file not found: {_WATCHLIST_FILE}",
        )
    registry = _get_registry()
    return {
        "status": "reloaded",
        "file": str(_WATCHLIST_FILE),
        "summary": registry.summary(),
        "total": registry.total,
    }
