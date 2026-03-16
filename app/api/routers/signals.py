"""
Signal Candidate Endpoints
===========================
GET  /signals/candidates           — list generated signal candidates (sample/preview)
GET  /signals/candidates/{asset}   — filter by asset symbol
POST /signals/evaluate             — evaluate a single document for signals
GET  /signals/historical/{asset}   — historical analogues for an asset
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()


class DocumentInput(BaseModel):
    """Input for single-document signal evaluation."""
    document_id: str = "eval-doc"
    source_id: str = "manual"
    title: str
    sentiment_label: str = "neutral"
    sentiment_score: float = 0.0
    impact_score: float = 0.5
    relevance_score: float = 0.5
    credibility_score: float = 0.7
    novelty_score: float = 1.0
    spam_probability: float = 0.05
    priority: str = "medium"
    affected_assets: list[str] = []
    matched_entities: list[str] = []
    bull_case: str = ""
    bear_case: str = ""
    url: str = ""


def _build_generator():
    from app.trading.signals.generator import SignalCandidateGenerator
    from app.trading.watchlists.watchlist import WatchlistRegistry
    from pathlib import Path
    registry = WatchlistRegistry.from_file(Path("monitor/watchlists.yml"))
    return SignalCandidateGenerator(watchlist=registry)


@router.get("/candidates")
async def list_candidates(
    min_confidence: float = Query(0.50, ge=0.0, le=1.0, description="Minimum signal confidence"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """
    List signal candidates from sample data.
    Connect to DB for production use.
    """
    from app.research.router_helpers import get_sample_candidates
    from app.analysis.ranking.trading_ranker import TradingRelevanceRanker

    candidates = get_sample_candidates()
    ranker = TradingRelevanceRanker()
    ranked = ranker.rank(candidates)
    filtered = [
        c.to_dict() | {"trading_relevance_score": round(score, 3)}
        for c, score in ranked
        if c.confidence >= min_confidence
    ][:limit]

    return {
        "candidates": filtered,
        "total": len(filtered),
        "note": "Sample data — connect DB for live signals",
    }


@router.get("/candidates/{asset}")
async def candidates_for_asset(
    asset: str,
    min_confidence: float = Query(0.40, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """List signal candidates for a specific asset."""
    from app.research.router_helpers import get_sample_candidates
    from app.analysis.ranking.trading_ranker import TradingRelevanceRanker

    symbol = asset.upper()
    all_candidates = get_sample_candidates()
    asset_candidates = [c for c in all_candidates if c.asset == symbol]

    if not asset_candidates:
        raise HTTPException(
            status_code=404,
            detail=f"No candidates for asset '{symbol}'.",
        )

    ranker = TradingRelevanceRanker()
    ranked = ranker.rank(asset_candidates)

    return {
        "asset": symbol,
        "candidates": [
            c.to_dict() | {"trading_relevance_score": round(score, 3)}
            for c, score in ranked
            if c.confidence >= min_confidence
        ],
        "total": len(ranked),
    }


@router.post("/evaluate")
async def evaluate_document(doc: DocumentInput) -> dict[str, Any]:
    """
    Evaluate a single document and return signal candidates.
    No DB required — pure in-memory evaluation.
    """
    from app.alerts.evaluator import DocumentScores
    from app.core.enums import DocumentPriority
    from app.analysis.ranking.trading_ranker import TradingRelevanceRanker

    priority_map = {
        "critical": DocumentPriority.CRITICAL,
        "high": DocumentPriority.HIGH,
        "medium": DocumentPriority.MEDIUM,
        "low": DocumentPriority.LOW,
        "noise": DocumentPriority.NOISE,
    }

    scores = DocumentScores(
        document_id=doc.document_id,
        source_id=doc.source_id,
        title=doc.title,
        sentiment_label=doc.sentiment_label,
        sentiment_score=doc.sentiment_score,
        impact_score=doc.impact_score,
        relevance_score=doc.relevance_score,
        credibility_score=doc.credibility_score,
        novelty_score=doc.novelty_score,
        spam_probability=doc.spam_probability,
        recommended_priority=priority_map.get(doc.priority.lower(), DocumentPriority.MEDIUM),
        affected_assets=doc.affected_assets,
        matched_entities=doc.matched_entities,
        bull_case=doc.bull_case,
        bear_case=doc.bear_case,
        url=doc.url,
    )

    generator = _build_generator()
    candidates = generator.generate(scores)

    ranker = TradingRelevanceRanker()
    ranked = ranker.rank(candidates)

    return {
        "document_id": doc.document_id,
        "title": doc.title,
        "candidates_generated": len(candidates),
        "candidates": [
            c.to_dict() | {"trading_relevance_score": round(score, 3)}
            for c, score in ranked
        ],
    }


@router.get("/historical/{asset}")
async def historical_analogues(
    asset: str,
    event_type: str | None = Query(None, description="Filter by event type"),
    sentiment: str | None = Query(None, description="positive | negative | neutral"),
) -> dict[str, Any]:
    """Find historical market analogues for an asset."""
    from app.analysis.historical.matcher import HistoricalMatcher

    symbol = asset.upper()
    matcher = HistoricalMatcher()
    analogues = matcher.find(
        assets=[symbol],
        event_type=event_type,
        sentiment=sentiment,
        max_results=5,
    )

    return {
        "asset": symbol,
        "analogues": [a.to_dict() for a in analogues],
        "total": len(analogues),
        "disclaimer": (
            "Historical analogues are for research context only. "
            "Past events do not predict future outcomes."
        ),
    }
