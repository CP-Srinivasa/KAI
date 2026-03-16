"""Shared helpers for research/signals API routers."""

from __future__ import annotations

from pathlib import Path

from app.alerts.evaluator import DocumentScores
from app.core.enums import DocumentPriority
from app.trading.signals.candidate import SignalCandidate
from app.trading.signals.generator import SignalCandidateGenerator
from app.trading.watchlists.watchlist import WatchlistRegistry


def get_sample_candidates() -> list[SignalCandidate]:
    """Return a set of sample signal candidates for demo endpoints."""
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
        DocumentScores(
            document_id="sample-003",
            source_id="theblock",
            title="SEC Opens Investigation Into Major DeFi Protocol",
            explanation_short="Regulatory scrutiny increases on decentralized exchanges.",
            sentiment_label="negative",
            sentiment_score=-0.70,
            impact_score=0.75,
            relevance_score=0.80,
            credibility_score=0.83,
            novelty_score=0.85,
            spam_probability=0.03,
            recommended_priority=DocumentPriority.HIGH,
            affected_assets=["ETH", "LINK"],
            matched_entities=["SEC"],
            bull_case="May accelerate compliance-focused DeFi development.",
            bear_case="Enforcement risk could suppress DeFi activity broadly.",
        ),
    ]

    candidates = []
    for scores in sample_scores:
        candidates.extend(generator.generate(scores))
    return candidates
