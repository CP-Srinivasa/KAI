"""Asset-diversification read API.

Backward-compatible, read-only surface for the dashboard diversification panel.

Endpoints
---------
- GET /api/diversification/overview
    Full concentration overview for the paper book: asset/sector/correlation
    distribution, short-term vs reserve split, cluster warnings, signal source
    breakdown and diversified scan candidates.

- GET /api/diversification/candidates?limit=N
    Ranked, diversified short-term scan candidates (the broaden-beyond-BTC/ETH
    pool) with per-candidate reasons.

- GET /api/diversification/universe
    The asset universe metadata (sector, horizon, tiers, tradability, score).

Auth: same CF-Access path as the other read routers (middleware in main.py).
No execution, no estimation of missing data.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from app.execution.portfolio_read import build_portfolio_snapshot
from app.trading.asset_universe import get_asset_universe
from app.trading.candidate_selector import select_short_term_candidates
from app.trading.diversification import exposures_from_snapshot
from app.trading.diversification_service import build_diversification_overview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diversification", tags=["diversification"])


@router.get("/overview")
async def diversification_overview() -> dict[str, Any]:
    """Concentration + diversification overview for the paper book."""
    try:
        return await build_diversification_overview()
    except Exception as exc:  # noqa: BLE001 — read surface must not 500 the dashboard
        logger.warning("[API] diversification overview failed: %s", exc)
        return {
            "report_type": "diversification_overview",
            "available": False,
            "error": f"overview_unavailable:{exc.__class__.__name__}",
        }


@router.get("/candidates")
async def diversification_candidates(limit: int = 6) -> dict[str, Any]:
    """Ranked diversified short-term scan candidates."""
    safe_limit = max(1, min(25, limit))
    try:
        snapshot = await build_portfolio_snapshot()
        rankings = select_short_term_candidates(
            positions=exposures_from_snapshot(snapshot), limit=safe_limit
        )
        return {
            "report_type": "diversification_candidates",
            "available": True,
            "limit": safe_limit,
            "candidates": [c.to_json_dict() for c in rankings],
            "selected_symbols": [c.symbol for c in rankings if c.included],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[API] diversification candidates failed: %s", exc)
        return {
            "report_type": "diversification_candidates",
            "available": False,
            "error": f"candidates_unavailable:{exc.__class__.__name__}",
        }


@router.get("/universe")
async def diversification_universe() -> dict[str, Any]:
    """Asset universe metadata (sector/horizon/tiers/tradability/score)."""
    try:
        universe = get_asset_universe()
        return {
            "report_type": "asset_universe",
            "available": True,
            "asset_count": len(universe.all()),
            "assets": [m.to_json_dict() for m in universe.all()],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[API] universe read failed: %s", exc)
        return {
            "report_type": "asset_universe",
            "available": False,
            "error": f"universe_unavailable:{exc.__class__.__name__}",
        }
