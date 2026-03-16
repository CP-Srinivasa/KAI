"""
Analysis Endpoints
==================
GET  /analysis/pending          — list documents pending analysis
POST /analysis/run              — trigger analysis run (background)
GET  /analysis/stats            — provider usage stats

[REQUIRES: OPENAI_API_KEY for LLM analysis]
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AnalysisStatus
from app.storage.db.session import get_db_session
from app.storage.models.db_models import CanonicalDocumentDB, DocumentAnalysisDB

router = APIRouter()


@router.get("/pending")
async def list_pending(
    limit: int = Query(50, ge=1, le=200),
    source_id: str | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """List documents awaiting LLM/rule-based analysis."""
    stmt = (
        select(CanonicalDocumentDB)
        .where(
            CanonicalDocumentDB.analysis_status == AnalysisStatus.PENDING.value,
            CanonicalDocumentDB.is_duplicate.is_(False),
        )
        .order_by(CanonicalDocumentDB.published_at.desc().nullslast())
        .limit(limit)
    )
    if source_id:
        stmt = stmt.where(CanonicalDocumentDB.source_id == source_id)

    result = await session.execute(stmt)
    docs = list(result.scalars().all())

    return {
        "pending": [
            {
                "id": str(d.id),
                "source_id": d.source_id,
                "title": d.title,
                "published_at": d.published_at.isoformat() if d.published_at else None,
                "analysis_status": d.analysis_status,
            }
            for d in docs
        ],
        "count": len(docs),
    }


@router.get("/stats")
async def analysis_stats(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Return analysis coverage statistics."""
    # Count by status
    status_result = await session.execute(
        select(
            CanonicalDocumentDB.analysis_status,
            func.count().label("count"),
        )
        .where(CanonicalDocumentDB.is_duplicate.is_(False))
        .group_by(CanonicalDocumentDB.analysis_status)
    )
    by_status = {row.analysis_status: row.count for row in status_result}

    # Total analyses done
    total_analyses = await session.execute(select(func.count()).select_from(DocumentAnalysisDB))
    total = total_analyses.scalar_one() or 0

    # Cost summary (if tracked)
    cost_result = await session.execute(
        select(
            func.sum(DocumentAnalysisDB.cost_usd).label("total_cost"),
            func.sum(DocumentAnalysisDB.token_count).label("total_tokens"),
            DocumentAnalysisDB.analyzed_by,
        )
        .group_by(DocumentAnalysisDB.analyzed_by)
    )
    by_provider: list[dict] = []
    for row in cost_result:
        by_provider.append({
            "analyzed_by": row.analyzed_by or "unknown",
            "total_cost_usd": round(row.total_cost or 0.0, 6),
            "total_tokens": row.total_tokens or 0,
        })

    return {
        "by_status": by_status,
        "total_analyses": total,
        "by_provider": by_provider,
    }


@router.post("/run")
async def trigger_analysis_run(
    batch_size: int = Query(50, ge=1, le=200, description="Max documents to analyze"),
    source_id: str | None = Query(None, description="Limit to specific source"),
    llm_enabled: bool = Query(False, description="[REQUIRES: OPENAI_API_KEY] Enable LLM analysis"),
) -> dict[str, Any]:
    """
    Trigger an analysis run.

    By default runs rule-based analysis only.
    Set llm_enabled=true to use LLM (requires OPENAI_API_KEY in .env).

    Note: For production use, run via the scheduler or CLI instead.
    """
    return {
        "status": "not_implemented",
        "message": (
            "Use CLI: `trading-bot analyze pending` or the APScheduler "
            "to run analysis. Direct API trigger coming in Phase 4."
        ),
        "llm_enabled": llm_enabled,
        "batch_size": batch_size,
    }
