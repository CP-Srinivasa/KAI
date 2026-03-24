"""Signal Candidates generation.

Extracts strictly actionable documents from the analysis pipeline into
a highly structured, rigid format for controlled signal-consumption layers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain.document import CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel


class SignalCandidate(BaseModel):
    """A strictly filtered, highly actionable setup extracted from analysis."""

    model_config = ConfigDict(strict=True, validate_assignment=True)

    signal_id: str
    document_id: str

    # Required heuristic signal fields
    target_asset: str
    direction_hint: str
    confidence: float
    supporting_evidence: str
    contradicting_evidence: str
    risk_notes: str
    source_quality: float
    recommended_next_step: str
    analysis_source: str

    # Research metrics - NOT execution instructions
    priority: int = Field(ge=0, le=10)
    sentiment: SentimentLabel
    affected_assets: list[str]
    market_scope: MarketScope

    published_at: datetime | None
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def extract_signal_candidates(
    documents: list[CanonicalDocument],
    min_priority: int = 8,
    watchlist_boosts: dict[str, int] | None = None,
) -> list[SignalCandidate]:
    """Parse a batch of documents and return viable Signal Candidates.

    Watchlists can be passed as a dictionary like {"BTC": 1} to artificially
    boost priority for specific assets during signaling, allowing them to
    clear the min_priority hurdle.
    """
    candidates: list[SignalCandidate] = []
    watchlist_boosts = watchlist_boosts or {}

    for doc in documents:
        if not doc.is_analyzed:
            continue

        base_priority = doc.priority_score or 0
        assets = list(set(doc.tickers + doc.crypto_assets))

        boost = max([watchlist_boosts.get(asset.upper(), 0) for asset in assets] + [0])
        effective_priority = min(10, base_priority + boost)

        if effective_priority < min_priority:
            continue

        direction = "neutral"
        if doc.sentiment_label == SentimentLabel.BULLISH:
            direction = "bullish"
        elif doc.sentiment_label == SentimentLabel.BEARISH:
            direction = "bearish"

        primary_asset = assets[0] if assets else "General Market"
        confidence_proxy = doc.relevance_score or 0.5

        candidates.append(
            SignalCandidate(
                signal_id=f"sig_{doc.id}",
                document_id=str(doc.id),
                target_asset=primary_asset,
                direction_hint=direction,
                confidence=confidence_proxy,
                supporting_evidence=doc.summary or doc.title or "No summary available.",
                contradicting_evidence="Contradicting evidence not extracted in primary scan.",
                risk_notes=(
                    f"spam_prob={doc.spam_probability or 0.0:.2f} scope={doc.market_scope.value}"
                ),
                source_quality=doc.credibility_score or 0.5,
                recommended_next_step=(
                    f"Review {direction} signal for {primary_asset} - human decision required."
                ),
                analysis_source=doc.effective_analysis_source.value,
                priority=effective_priority,
                sentiment=doc.sentiment_label or SentimentLabel.NEUTRAL,
                affected_assets=assets,
                market_scope=doc.market_scope or MarketScope.UNKNOWN,
                published_at=doc.published_at,
            )
        )

    candidates.sort(key=lambda signal: signal.priority, reverse=True)
    return candidates
