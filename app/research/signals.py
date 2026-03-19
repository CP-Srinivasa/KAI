"""Signal Candidates generation.

Extracts strictly actionable documents from the analysis pipeline into
a highly structured, rigid format aimed at trading execution layers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain.document import CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel


class SignalCandidate(BaseModel):
    """A strictly filtered, highly actionable setup extracted from analysis.

    This is what the execution bot will eventually ingest.
    """

    model_config = ConfigDict(strict=True, validate_assignment=True)

    signal_id: str
    document_id: str
    title: str
    summary: str

    # Execution metrics
    priority: int = Field(ge=8, le=10)
    sentiment: SentimentLabel
    action_direction: str  # "buy", "sell", "hold" (derived from sentiment)
    affected_assets: list[str]
    market_scope: MarketScope

    published_at: datetime | None
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def extract_signal_candidates(
    documents: list[CanonicalDocument],
    min_priority: int = 8,
) -> list[SignalCandidate]:
    """Parse a batch of documents and return only the viable Signal Candidates."""
    candidates: list[SignalCandidate] = []

    for doc in documents:
        if not doc.is_analyzed:
            continue

        priority = doc.priority_score or 0
        if priority < min_priority:
            continue

        # Optional: Additional signal restrictions (must be explicitly marked actionable by LLM)
        # Note: Depending on scoring, min_priority=8 usually implies actionable anyway.

        direction = "hold"
        if doc.sentiment_label == SentimentLabel.BULLISH:
            direction = "buy"
        elif doc.sentiment_label == SentimentLabel.BEARISH:
            direction = "sell"

        assets = list(set(doc.tickers + doc.crypto_assets))

        candidates.append(
            SignalCandidate(
                signal_id=f"sig_{doc.id}",
                document_id=str(doc.id),
                title=doc.title or "(No Title)",
                summary=doc.summary or doc.title[:100] or "",
                priority=priority,
                sentiment=doc.sentiment_label or SentimentLabel.NEUTRAL,
                action_direction=direction,
                affected_assets=assets,
                market_scope=doc.market_scope or MarketScope.UNKNOWN,
                published_at=doc.published_at,
            )
        )

    # Sort with highest priority first
    candidates.sort(key=lambda s: s.priority, reverse=True)
    return candidates
