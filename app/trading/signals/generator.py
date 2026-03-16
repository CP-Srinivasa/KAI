"""
Signal Candidate Generator
===========================
Generates SignalCandidate objects from DocumentScores + AlertDecision.

No orders are generated here. This is pure research output.

Pipeline:
  1. EventToAssetMapper selects affected assets with confidence
  2. For each asset above threshold → build a SignalCandidate
  3. Infer direction_hint from sentiment
  4. Infer urgency from priority + alert_type
  5. Infer narrative_label from tags + entities
  6. Populate evidence lists from bull/bear case (if LLM analysis present)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.alerts.evaluator import AlertDecision, DocumentScores
from app.core.enums import (
    AlertType,
    DirectionHint,
    DocumentPriority,
    EventType,
    NarrativeLabel,
    SignalUrgency,
)
from app.core.logging import get_logger
from app.trading.event_to_signal.mapper import AssetMapping, EventToAssetMapper
from app.trading.signals.candidate import SignalCandidate
from app.trading.watchlists.watchlist import WatchlistRegistry

logger = get_logger(__name__)


# ─── Mapping helpers ─────────────────────────────────────────────────────────

def _direction_from_sentiment(label: str, score: float) -> DirectionHint:
    """Derive bullish/bearish direction from sentiment."""
    label = label.lower()
    if label == "positive" and score > 0.3:
        return DirectionHint.BULLISH
    if label == "negative" and score < -0.3:
        return DirectionHint.BEARISH
    if abs(score) < 0.15:
        return DirectionHint.NEUTRAL
    return DirectionHint.MIXED


def _urgency_from_priority(priority: DocumentPriority, alert_type: AlertType | None) -> SignalUrgency:
    """Map document priority to signal urgency."""
    if alert_type in (AlertType.BREAKING, AlertType.WATCHLIST_HIT):
        if priority == DocumentPriority.CRITICAL:
            return SignalUrgency.IMMEDIATE
        if priority == DocumentPriority.HIGH:
            return SignalUrgency.SHORT_TERM
    if priority == DocumentPriority.CRITICAL:
        return SignalUrgency.SHORT_TERM
    if priority == DocumentPriority.HIGH:
        return SignalUrgency.SHORT_TERM
    if priority == DocumentPriority.MEDIUM:
        return SignalUrgency.MEDIUM_TERM
    if priority == DocumentPriority.LOW:
        return SignalUrgency.LONG_TERM
    return SignalUrgency.MONITOR


def _narrative_from_context(
    entities: list[str],
    tags: list[str],
    event_type: str | None,
    title: str,
) -> NarrativeLabel:
    """Infer narrative label from available context signals."""
    combined = " ".join(entities + tags + ([event_type] if event_type else []) + [title]).lower()

    rules: list[tuple[list[str], NarrativeLabel]] = [
        (["hack", "exploit", "breach", "rug"], NarrativeLabel.HACK_EXPLOIT),
        (["regulatory", "regulation", "sec", "ban", "cftc", "legal"], NarrativeLabel.REGULATORY_RISK),
        (["etf", "institutional", "blackrock", "fidelity", "adoption"], NarrativeLabel.INSTITUTIONAL_ADOPTION),
        (["crash", "collapse", "depeg", "liquidation", "crisis"], NarrativeLabel.MARKET_CRASH),
        (["recovery", "rebound", "bullrun", "bull run", "bounce"], NarrativeLabel.RECOVERY),
        (["fed", "rate", "inflation", "macro", "recession", "gdp"], NarrativeLabel.MACRO_SHIFT),
        (["liquidity", "stablecoin", "depeg", "tether", "usdc"], NarrativeLabel.LIQUIDITY_CRISIS),
        (["upgrade", "fork", "protocol", "l2", "layer2", "scaling"], NarrativeLabel.TECH_UPGRADE),
        (["ecosystem", "partnership", "launch", "adoption"], NarrativeLabel.ECOSYSTEM_GROWTH),
        (["sentiment", "fear", "greed", "social", "twitter", "reddit"], NarrativeLabel.SENTIMENT_SHIFT),
    ]
    for keywords, label in rules:
        if any(kw in combined for kw in keywords):
            return label
    return NarrativeLabel.UNKNOWN


def _recommended_next_step(direction: DirectionHint, urgency: SignalUrgency, asset: str) -> str:
    """Generate a plain-language research action recommendation."""
    if urgency == SignalUrgency.IMMEDIATE:
        return f"Review latest {asset} order book depth and on-chain flows immediately."
    if direction == DirectionHint.BULLISH and urgency == SignalUrgency.SHORT_TERM:
        return f"Monitor {asset} for follow-through confirmation over next 1–3 days before any position sizing."
    if direction == DirectionHint.BEARISH:
        return f"Assess {asset} downside exposure; review stop levels and sector correlation."
    if urgency == SignalUrgency.MEDIUM_TERM:
        return f"Add {asset} to watch list for medium-term thesis development."
    return f"Continue monitoring {asset} — insufficient confidence for near-term action."


@dataclass
class GeneratorConfig:
    min_confidence: float = 0.55       # Skip assets below this confidence
    max_assets_per_doc: int = 4        # Cap candidates per document
    min_impact_score: float = 0.30     # Skip if document has too low impact


class SignalCandidateGenerator:
    """
    Generates SignalCandidate list from a scored document.

    Usage:
        generator = SignalCandidateGenerator(watchlist=registry)
        candidates = generator.generate(scores, decision)
    """

    def __init__(
        self,
        watchlist: WatchlistRegistry | None = None,
        config: GeneratorConfig | None = None,
    ) -> None:
        self._mapper = EventToAssetMapper(watchlist=watchlist)
        self._config = config or GeneratorConfig()

    def generate(
        self,
        scores: DocumentScores,
        decision: AlertDecision | None = None,
    ) -> list[SignalCandidate]:
        """
        Generate signal candidates for a document.

        Returns empty list if document doesn't meet minimum quality bar.
        """
        cfg = self._config

        # Quality gate
        if scores.impact_score < cfg.min_impact_score:
            logger.debug(
                "signal_skipped_low_impact",
                doc=scores.document_id,
                impact=scores.impact_score,
            )
            return []

        # Get asset mappings
        text = f"{scores.title} {getattr(scores, 'explanation_short', '')}"
        asset_mappings = self._mapper.top_assets(
            text=text,
            matched_entities=scores.matched_entities,
            matched_tags=getattr(scores, "affected_sectors", []),
            affected_assets=getattr(scores, "affected_assets", []),
            min_confidence=cfg.min_confidence,
            max_results=cfg.max_assets_per_doc,
        )

        if not asset_mappings:
            logger.debug("signal_no_assets_mapped", doc=scores.document_id)
            return []

        alert_type = decision.alert_type if decision else None
        candidates = []

        for mapping in asset_mappings:
            candidate = self._build_candidate(scores, mapping, alert_type)
            candidates.append(candidate)

        logger.info(
            "signals_generated",
            doc=scores.document_id,
            count=len(candidates),
            assets=[c.asset for c in candidates],
        )
        return candidates

    def _build_candidate(
        self,
        scores: DocumentScores,
        mapping: AssetMapping,
        alert_type: AlertType | None,
    ) -> SignalCandidate:
        direction = _direction_from_sentiment(
            scores.sentiment_label,
            scores.sentiment_score,
        )
        urgency = _urgency_from_priority(scores.recommended_priority, alert_type)
        narrative = _narrative_from_context(
            entities=scores.matched_entities,
            tags=getattr(scores, "affected_sectors", []),
            event_type=getattr(scores, "event_type", None),
            title=scores.title,
        )

        # Build evidence lists from LLM output if available
        bull = getattr(scores, "bull_case", "") or ""
        bear = getattr(scores, "bear_case", "") or ""
        supporting = [bull] if bull else []
        contradicting = [bear] if bear else []

        # Add mapping reason as supporting context
        if mapping.reason:
            supporting.append(f"Asset link: {mapping.reason}")

        # Risk notes
        risk_notes: list[str] = []
        if scores.credibility_score < 0.60:
            risk_notes.append(f"Low source credibility ({scores.credibility_score:.0%})")
        if scores.spam_probability > 0.30:
            risk_notes.append(f"Elevated spam probability ({scores.spam_probability:.0%})")
        if scores.novelty_score < 0.40:
            risk_notes.append("Low novelty — may be recycled news")
        if mapping.mapping_type == "thematic":
            risk_notes.append(f"Indirect asset link via thematic mapping (confidence {mapping.confidence:.0%})")

        return SignalCandidate(
            document_id=scores.document_id,
            source_id=scores.source_id,
            asset=mapping.asset,
            direction_hint=direction,
            confidence=round(
                mapping.confidence * scores.impact_score * 0.5
                + mapping.confidence * 0.5,
                3,
            ),
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            risk_notes=risk_notes,
            source_quality=scores.credibility_score,
            historical_context=getattr(scores, "historical_context", ""),
            narrative_label=narrative,
            urgency=urgency,
            severity=scores.recommended_priority,
            recommended_next_step=_recommended_next_step(direction, urgency, mapping.asset),
            title=scores.title,
            url=getattr(scores, "url", ""),
            sentiment_label=scores.sentiment_label,
            sentiment_score=scores.sentiment_score,
            impact_score=scores.impact_score,
            relevance_score=scores.relevance_score,
            matched_entities=scores.matched_entities,
        )
