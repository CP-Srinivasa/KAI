"""
Research Output Endpoints
==========================
GET  /research/brief               — daily research brief (sample data)
GET  /research/asset/{symbol}      — asset research pack for a symbol
POST /research/generate            — generate brief from provided scores
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ScoresInput(BaseModel):
    """Minimal document scores input for research generation."""
    document_id: str = "input-doc"
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
    affected_sectors: list[str] = []
    matched_entities: list[str] = []
    bull_case: str = ""
    bear_case: str = ""
    url: str = ""


def _sample_candidates():
    """Return sample signal candidates for demo/preview endpoints."""
    from app.alerts.evaluator import DocumentScores
    from app.core.enums import DocumentPriority
    from app.trading.signals.generator import SignalCandidateGenerator
    from app.trading.watchlists.watchlist import WatchlistRegistry
    from pathlib import Path

    registry = WatchlistRegistry.from_file(Path("monitor/watchlists.yml"))
    generator = SignalCandidateGenerator(watchlist=registry)

    sample_scores = [
        DocumentScores(
            document_id="sample-001",
            source_id="coindesk_rss",
            title="BlackRock Bitcoin ETF Surpasses $10B in AUM",
            explanation_short="iShares Bitcoin Trust reaches major milestone.",
            sentiment_label="positive",
            sentiment_score=0.82,
            impact_score=0.85,
            relevance_score=0.90,
            credibility_score=0.88,
            novelty_score=0.92,
            spam_probability=0.02,
            recommended_priority=DocumentPriority.HIGH,
            affected_assets=["BTC", "IBIT"],
            matched_entities=["BlackRock", "Bitcoin"],
            bull_case="Continued institutional inflows signal mainstream adoption.",
            bear_case="Profit-taking possible after milestone.",
        ),
        DocumentScores(
            document_id="sample-002",
            source_id="reuters",
            title="Fed Holds Rates Steady, Signals Two Cuts in 2024",
            explanation_short="FOMC maintains policy rate, hints at future easing.",
            sentiment_label="positive",
            sentiment_score=0.55,
            impact_score=0.78,
            relevance_score=0.72,
            credibility_score=0.95,
            novelty_score=0.88,
            spam_probability=0.01,
            recommended_priority=DocumentPriority.HIGH,
            affected_assets=["BTC", "ETH", "NVDA"],
            matched_entities=["Federal Reserve", "Powell"],
            bull_case="Rate cuts are historically bullish for risk assets.",
            bear_case="Cuts may be priced in; execution risk remains.",
        ),
    ]

    candidates = []
    for scores in sample_scores:
        candidates.extend(generator.generate(scores))
    return candidates


@router.get("/brief")
async def daily_brief() -> dict[str, Any]:
    """
    Generate a daily research brief from sample data.
    For real data, connect to the database and pass actual scored documents.
    """
    from app.research.builder import ResearchPackBuilder
    from datetime import datetime

    candidates = _sample_candidates()
    builder = ResearchPackBuilder()
    brief = builder.daily_brief(candidates, date=datetime.utcnow().strftime("%Y-%m-%d"))
    return brief.to_dict()


@router.get("/asset/{symbol}")
async def asset_research(symbol: str) -> dict[str, Any]:
    """
    Research pack for a specific asset symbol (e.g. BTC, ETH, NVDA).
    Uses sample data — connect DB for real analysis.
    """
    symbol_upper = symbol.upper()
    candidates = _sample_candidates()
    asset_candidates = [c for c in candidates if c.asset == symbol_upper]

    if not asset_candidates:
        raise HTTPException(
            status_code=404,
            detail=f"No signal candidates found for asset '{symbol_upper}'. "
                   f"Available: {list({c.asset for c in candidates})}",
        )

    from app.research.builder import ResearchPackBuilder
    builder = ResearchPackBuilder()
    pack = builder.for_asset(symbol_upper, candidates)
    return pack.to_dict()


@router.post("/generate")
async def generate_research(inputs: list[ScoresInput]) -> dict[str, Any]:
    """
    Generate a research brief from provided document scores.
    Useful for testing the pipeline without a live database.
    """
    from app.alerts.evaluator import DocumentScores
    from app.core.enums import DocumentPriority
    from app.trading.signals.generator import SignalCandidateGenerator
    from app.trading.watchlists.watchlist import WatchlistRegistry
    from app.research.builder import ResearchPackBuilder
    from pathlib import Path

    registry = WatchlistRegistry.from_file(Path("monitor/watchlists.yml"))
    generator = SignalCandidateGenerator(watchlist=registry)
    builder = ResearchPackBuilder()

    priority_map = {
        "critical": DocumentPriority.CRITICAL,
        "high": DocumentPriority.HIGH,
        "medium": DocumentPriority.MEDIUM,
        "low": DocumentPriority.LOW,
        "noise": DocumentPriority.NOISE,
    }

    candidates = []
    for inp in inputs:
        scores = DocumentScores(
            document_id=inp.document_id,
            source_id=inp.source_id,
            title=inp.title,
            sentiment_label=inp.sentiment_label,
            sentiment_score=inp.sentiment_score,
            impact_score=inp.impact_score,
            relevance_score=inp.relevance_score,
            credibility_score=inp.credibility_score,
            novelty_score=inp.novelty_score,
            spam_probability=inp.spam_probability,
            recommended_priority=priority_map.get(inp.priority.lower(), DocumentPriority.MEDIUM),
            affected_assets=inp.affected_assets,
            matched_entities=inp.matched_entities,
            bull_case=inp.bull_case,
            bear_case=inp.bear_case,
            url=inp.url,
        )
        candidates.extend(generator.generate(scores))

    if not candidates:
        return {
            "message": "No signal candidates generated. Check impact_score thresholds and asset coverage.",
            "total_signals": 0,
        }

    from datetime import datetime
    brief = builder.daily_brief(candidates, date=datetime.utcnow().strftime("%Y-%m-%d"))
    result = brief.to_dict()
    result["note"] = f"Generated from {len(inputs)} input document(s), {len(candidates)} signal candidate(s)"
    return result
