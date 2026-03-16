"""
Priority Composer
=================
Combines all scoring signals into a final DocumentPriority enum value.

Score breakdown:
  - keyword_score:     Keyword/entity match strength (from KeywordMatcher)
  - relevance_score:   LLM relevance score (or 0.5 if not yet analyzed)
  - impact_score:      LLM impact score (or rule-based fallback)
  - recency_score:     Exponential decay from ranker.py
  - credibility_score: Source + content credibility
  - novelty_score:     How new/unseen the content is

Thresholds map the composite score → DocumentPriority:
  ≥ 0.80 → CRITICAL
  ≥ 0.60 → HIGH
  ≥ 0.40 → MEDIUM
  ≥ 0.20 → LOW
  <  0.20 → NOISE
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import DocumentPriority


@dataclass
class ScoreBundle:
    """All partial scores for a document, before composition."""
    keyword_score: float = 0.0       # [0.0, 1.0] keyword/entity hit strength
    relevance_score: float = 0.5     # [0.0, 1.0] LLM or heuristic relevance
    impact_score: float = 0.0        # [0.0, 1.0] LLM or heuristic impact
    recency_score: float = 1.0       # [0.0, 1.0] exponential decay
    credibility_score: float = 0.5   # [0.0, 1.0] source + content
    novelty_score: float = 1.0       # [0.0, 1.0] how unseen this is


_PRIORITY_THRESHOLDS = [
    (0.80, DocumentPriority.CRITICAL),
    (0.60, DocumentPriority.HIGH),
    (0.40, DocumentPriority.MEDIUM),
    (0.20, DocumentPriority.LOW),
]


class PriorityComposer:
    """
    Weighted combination of all scoring signals → DocumentPriority.

    Weights should sum to 1.0. Adjust via constructor to tune behavior.
    """

    def __init__(
        self,
        w_keyword: float = 0.30,
        w_relevance: float = 0.20,
        w_impact: float = 0.20,
        w_recency: float = 0.15,
        w_credibility: float = 0.10,
        w_novelty: float = 0.05,
    ) -> None:
        self._w_keyword = w_keyword
        self._w_relevance = w_relevance
        self._w_impact = w_impact
        self._w_recency = w_recency
        self._w_credibility = w_credibility
        self._w_novelty = w_novelty

    def compute_score(self, bundle: ScoreBundle) -> float:
        """Compute composite priority score [0.0, 1.0]."""
        raw = (
            self._w_keyword     * bundle.keyword_score
            + self._w_relevance * bundle.relevance_score
            + self._w_impact    * bundle.impact_score
            + self._w_recency   * bundle.recency_score
            + self._w_credibility * bundle.credibility_score
            + self._w_novelty   * bundle.novelty_score
        )
        return round(min(1.0, max(0.0, raw)), 4)

    def classify(self, bundle: ScoreBundle) -> DocumentPriority:
        """Map score bundle → DocumentPriority enum."""
        score = self.compute_score(bundle)
        for threshold, priority in _PRIORITY_THRESHOLDS:
            if score >= threshold:
                return priority
        return DocumentPriority.NOISE

    def classify_with_score(self, bundle: ScoreBundle) -> tuple[DocumentPriority, float]:
        """Return both priority and composite score."""
        score = self.compute_score(bundle)
        for threshold, priority in _PRIORITY_THRESHOLDS:
            if score >= threshold:
                return priority, score
        return DocumentPriority.NOISE, score
