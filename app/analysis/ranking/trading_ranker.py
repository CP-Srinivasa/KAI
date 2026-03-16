"""
Trading Relevance Ranker
=========================
Ranks signal candidates and document scores by trading relevance.

Ranking factors (all weighted):
  - signal_confidence   — quality of the signal itself
  - asset_coverage      — how many watchlist assets are affected
  - urgency             — immediacy of the signal
  - impact              — underlying document impact score
  - novelty             — is this new information?
  - source_quality      — credibility of the source

Used by: ResearchPackBuilder, API /signals/candidates, CLI signals generate.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import SignalUrgency
from app.core.logging import get_logger
from app.trading.signals.candidate import SignalCandidate

logger = get_logger(__name__)

_URGENCY_WEIGHT = {
    SignalUrgency.IMMEDIATE: 1.0,
    SignalUrgency.SHORT_TERM: 0.80,
    SignalUrgency.MEDIUM_TERM: 0.55,
    SignalUrgency.LONG_TERM: 0.30,
    SignalUrgency.MONITOR: 0.10,
}


@dataclass
class RankingWeights:
    confidence: float = 0.30
    urgency: float = 0.25
    impact: float = 0.25
    source_quality: float = 0.12
    novelty: float = 0.08


class TradingRelevanceRanker:
    """
    Ranks SignalCandidates by composite trading relevance score.

    Higher score = more relevant for near-term research / decision-making.
    """

    def __init__(self, weights: RankingWeights | None = None) -> None:
        self._w = weights or RankingWeights()

    def score(self, candidate: SignalCandidate, novelty: float = 1.0) -> float:
        """Compute a composite trading relevance score (0–1)."""
        urgency_val = _URGENCY_WEIGHT.get(candidate.urgency, 0.10)
        result = (
            self._w.confidence * candidate.confidence
            + self._w.urgency * urgency_val
            + self._w.impact * candidate.impact_score
            + self._w.source_quality * candidate.source_quality
            + self._w.novelty * novelty
        )
        return round(min(1.0, result), 4)

    def rank(
        self,
        candidates: list[SignalCandidate],
        novelty_map: dict[str, float] | None = None,
    ) -> list[tuple[SignalCandidate, float]]:
        """
        Rank a list of candidates.

        Args:
            candidates:  List of SignalCandidates to rank.
            novelty_map: Optional {document_id: novelty_score} override.

        Returns:
            Sorted list of (candidate, score) tuples, highest first.
        """
        nmap = novelty_map or {}
        scored = [
            (c, self.score(c, nmap.get(c.document_id, 1.0)))
            for c in candidates
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        logger.debug(
            "candidates_ranked",
            total=len(scored),
            top_score=scored[0][1] if scored else 0.0,
        )
        return scored

    def top_n(
        self,
        candidates: list[SignalCandidate],
        n: int = 10,
        min_score: float = 0.30,
        novelty_map: dict[str, float] | None = None,
    ) -> list[SignalCandidate]:
        """Return top N candidates above min_score threshold."""
        ranked = self.rank(candidates, novelty_map)
        return [c for c, s in ranked if s >= min_score][:n]
