"""
Historical Analogue Matcher
============================
Finds historical event analogues for a current document using
keyword/entity overlap scoring — no ML model required.

Scoring factors:
  1. Asset overlap    — affected_assets ∩ historical_event.affected_assets
  2. Tag overlap      — document tags ∩ historical_event.tags
  3. Event type match — document event_type == historical_event.event_type
  4. Sentiment match  — same polarity (positive / negative / neutral)

Returns a list of HistoricalAnalogue objects sorted by similarity_score.

Usage:
    matcher = HistoricalMatcher(events=SEED_EVENTS)
    analogues = matcher.find(
        assets=["BTC"], tags=["regulatory"], event_type="regulatory",
        sentiment="negative"
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.storage.models.historical import HistoricalEvent

logger = get_logger(__name__)


@dataclass
class HistoricalAnalogue:
    """A matched historical event with similarity scoring."""

    event: HistoricalEvent
    similarity_score: float          # 0.0–1.0
    similarity_reason: str = ""
    matched_assets: list[str] = field(default_factory=list)
    matched_tags: list[str] = field(default_factory=list)
    outcome_summary: str = ""
    confidence_caveat: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event.id,
            "event_title": self.event.title,
            "occurred_at": self.event.occurred_at.isoformat(),
            "event_type": self.event.event_type,
            "market_scope": self.event.market_scope,
            "sentiment": self.event.sentiment_label,
            "similarity_score": round(self.similarity_score, 3),
            "similarity_reason": self.similarity_reason,
            "matched_assets": self.matched_assets,
            "matched_tags": self.matched_tags,
            "outcome_summary": self.event.outcome_summary or self.outcome_summary,
            "max_price_impact_pct": self.event.max_price_impact_pct,
            "resolution_days": self.event.resolution_days,
            "confidence_caveat": self.confidence_caveat,
        }


class HistoricalMatcher:
    """
    Pure in-memory historical event matcher.

    Initialize with a list of HistoricalEvent objects.
    For production use, load events from the database (HistoricalEventDB).
    """

    def __init__(
        self,
        events: list[HistoricalEvent] | None = None,
        min_similarity: float = 0.20,
    ) -> None:
        self._events = events or SEED_EVENTS
        self._min_similarity = min_similarity

    def find(
        self,
        assets: list[str] | None = None,
        tags: list[str] | None = None,
        event_type: str | None = None,
        sentiment: str | None = None,
        max_results: int = 3,
    ) -> list[HistoricalAnalogue]:
        """
        Find the best matching historical events.

        Args:
            assets:     Affected asset symbols (BTC, ETH…)
            tags:       Document/event tags for thematic matching
            event_type: EventType string (e.g. "regulatory")
            sentiment:  "positive" / "negative" / "neutral"
            max_results: Cap on returned analogues

        Returns:
            Sorted list of HistoricalAnalogue, highest score first.
        """
        assets_set = {a.upper() for a in (assets or [])}
        tags_set = {t.lower() for t in (tags or [])}
        results: list[HistoricalAnalogue] = []

        for event in self._events:
            score, reason, m_assets, m_tags = self._score(
                event, assets_set, tags_set, event_type, sentiment
            )
            if score < self._min_similarity:
                continue

            caveat = self._confidence_caveat(score, event)
            results.append(
                HistoricalAnalogue(
                    event=event,
                    similarity_score=score,
                    similarity_reason=reason,
                    matched_assets=m_assets,
                    matched_tags=m_tags,
                    confidence_caveat=caveat,
                )
            )

        results.sort(key=lambda a: a.similarity_score, reverse=True)
        logger.debug(
            "historical_analogues_found",
            total=len(results),
            assets=list(assets_set),
        )
        return results[:max_results]

    def _score(
        self,
        event: HistoricalEvent,
        assets_set: set[str],
        tags_set: set[str],
        event_type: str | None,
        sentiment: str | None,
    ) -> tuple[float, str, list[str], list[str]]:
        score = 0.0
        reasons: list[str] = []

        # Asset overlap (weight: 0.40)
        ev_assets = {a.upper() for a in event.affected_assets}
        m_assets = list(assets_set & ev_assets)
        if m_assets:
            overlap = len(m_assets) / max(len(assets_set | ev_assets), 1)
            score += 0.40 * overlap
            reasons.append(f"Asset overlap: {', '.join(m_assets)}")

        # Tag overlap (weight: 0.30)
        ev_tags = {t.lower() for t in event.tags}
        m_tags = list(tags_set & ev_tags)
        if m_tags:
            overlap = len(m_tags) / max(len(tags_set | ev_tags), 1)
            score += 0.30 * overlap
            reasons.append(f"Tag overlap: {', '.join(m_tags)}")

        # Event type match (weight: 0.20)
        if event_type and event.event_type:
            ev_type = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
            if event_type.lower() == ev_type.lower():
                score += 0.20
                reasons.append(f"Event type match: {event_type}")

        # Sentiment match (weight: 0.10)
        if sentiment and event.sentiment_label:
            ev_sent = event.sentiment_label.value if hasattr(event.sentiment_label, "value") else str(event.sentiment_label)
            if sentiment.lower() == ev_sent.lower():
                score += 0.10
                reasons.append("Sentiment match")

        return min(score, 1.0), "; ".join(reasons) or "weak pattern match", m_assets, m_tags

    def _confidence_caveat(self, score: float, event: HistoricalEvent) -> str:
        if score >= 0.70:
            return "Strong pattern match — outcomes may be informative but history does not repeat exactly."
        if score >= 0.40:
            return "Moderate similarity — treat as loose analogy only."
        return "Weak similarity — use as background context only, not predictive guidance."


# ─────────────────────────────────────────────
# Seed historical events (in-memory baseline)
# Production: load from HistoricalEventDB via repository
# ─────────────────────────────────────────────

from datetime import datetime as _dt
from app.core.enums import EventType, MarketScope, SentimentLabel

SEED_EVENTS: list[HistoricalEvent] = [
    HistoricalEvent(
        id="hist-001",
        title="Bitcoin ETF Approval (iShares / BlackRock)",
        description="SEC approved multiple spot Bitcoin ETFs including BlackRock's iShares Bitcoin Trust.",
        event_type=EventType.REGULATORY,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.POSITIVE,
        occurred_at=_dt(2024, 1, 10),
        affected_assets=["BTC", "IBIT", "FBTC", "GBTC"],
        affected_sectors=["crypto", "etf"],
        tags=["regulatory", "institutional_adoption", "bitcoin_etf", "sec"],
        outcome_summary="BTC rallied ~15% on day of approval. ETF inflows exceeded $1B in first week.",
        max_price_impact_pct=15.0,
        resolution_days=7,
    ),
    HistoricalEvent(
        id="hist-002",
        title="FTX Exchange Collapse",
        description="FTX exchange filed for bankruptcy amid liquidity crisis and fraud allegations.",
        event_type=EventType.LEGAL,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.NEGATIVE,
        occurred_at=_dt(2022, 11, 11),
        affected_assets=["BTC", "ETH", "SOL", "BNB"],
        affected_sectors=["crypto", "exchange", "defi"],
        tags=["collapse", "fraud", "exchange", "liquidity_crisis", "bear"],
        outcome_summary="BTC dropped ~25% in a week. Contagion spread to lenders and protocols.",
        max_price_impact_pct=-25.0,
        resolution_days=180,
    ),
    HistoricalEvent(
        id="hist-003",
        title="Terra/Luna UST Depeg",
        description="UST stablecoin lost its peg triggering a death spiral in LUNA.",
        event_type=EventType.MARKET_MANIPULATION,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.NEGATIVE,
        occurred_at=_dt(2022, 5, 9),
        affected_assets=["BTC", "ETH", "LUNA", "USDT"],
        affected_sectors=["crypto", "stablecoin", "defi"],
        tags=["depeg", "stablecoin", "collapse", "liquidity_crisis"],
        outcome_summary="LUNA went to near zero. BTC lost -30% in two weeks. Market-wide confidence crisis.",
        max_price_impact_pct=-80.0,
        resolution_days=365,
    ),
    HistoricalEvent(
        id="hist-004",
        title="Bitcoin Halving (April 2024)",
        description="Bitcoin block reward halved from 6.25 BTC to 3.125 BTC.",
        event_type=EventType.FORK_UPGRADE,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.POSITIVE,
        occurred_at=_dt(2024, 4, 20),
        affected_assets=["BTC", "MARA", "RIOT"],
        affected_sectors=["crypto", "mining"],
        tags=["halving", "supply", "bitcoin", "bullish_catalyst"],
        outcome_summary="Historically precedes bull run. 2024 cycle saw pre-halving ATH before typical post-halving run.",
        max_price_impact_pct=None,
        resolution_days=365,
    ),
    HistoricalEvent(
        id="hist-005",
        title="SEC vs Ripple Ruling (Summary Judgment)",
        description="Judge ruled XRP itself not a security on secondary market sales.",
        event_type=EventType.REGULATORY,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.POSITIVE,
        occurred_at=_dt(2023, 7, 13),
        affected_assets=["XRP", "BTC", "ETH"],
        affected_sectors=["crypto", "regulatory"],
        tags=["sec", "regulatory", "legal", "xrp"],
        outcome_summary="XRP surged +70% on ruling day. Positive signal for broader crypto regulation clarity.",
        max_price_impact_pct=70.0,
        resolution_days=30,
    ),
    HistoricalEvent(
        id="hist-006",
        title="Fed Emergency Rate Hike (March 2022)",
        description="Federal Reserve began aggressive rate hike cycle to combat inflation.",
        event_type=EventType.MACRO_ECONOMIC,
        market_scope=MarketScope.MIXED,
        sentiment_label=SentimentLabel.NEGATIVE,
        occurred_at=_dt(2022, 3, 16),
        affected_assets=["BTC", "ETH", "NVDA"],
        affected_sectors=["macro", "tech", "crypto"],
        tags=["fed", "rate_hike", "macro", "risk_off", "inflation"],
        outcome_summary="Risk assets sold off. BTC fell from $40K to ~$16K over the following months.",
        max_price_impact_pct=-60.0,
        resolution_days=365,
    ),
    HistoricalEvent(
        id="hist-007",
        title="Mt. Gox Hack",
        description="Mt. Gox exchange hacked, 850,000 BTC lost. Exchange filed for bankruptcy.",
        event_type=EventType.HACK_EXPLOIT,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.NEGATIVE,
        occurred_at=_dt(2014, 2, 24),
        affected_assets=["BTC"],
        affected_sectors=["crypto", "exchange"],
        tags=["hack", "exchange", "security", "bear"],
        outcome_summary="BTC dropped ~36% in weeks following disclosure. Years-long bear market followed.",
        max_price_impact_pct=-36.0,
        resolution_days=730,
    ),
    HistoricalEvent(
        id="hist-008",
        title="Ethereum Merge (PoS Transition)",
        description="Ethereum successfully transitioned from Proof of Work to Proof of Stake.",
        event_type=EventType.FORK_UPGRADE,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.POSITIVE,
        occurred_at=_dt(2022, 9, 15),
        affected_assets=["ETH", "MATIC", "LINK"],
        affected_sectors=["crypto", "defi", "layer1"],
        tags=["upgrade", "ethereum", "merge", "pos", "tech_upgrade"],
        outcome_summary="ETH priced in merge early; sold off after ('sell the news'). Long-term issuance reduced.",
        max_price_impact_pct=-8.0,
        resolution_days=60,
    ),
]
