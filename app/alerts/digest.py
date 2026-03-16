"""
Digest Builder
==============
Aggregates analyzed documents for scheduled digest alerts.

The DigestBuilder collects documents above a minimum threshold,
deduplicates and sorts them, and produces a ranked list ready
for formatting by the channel adapters.

Usage:
    builder = DigestBuilder(min_impact=0.3, min_relevance=0.3)
    items = builder.build(analyzed_docs)
    await dispatcher.dispatch_digest(items, channels=[AlertChannel.TELEGRAM])
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.alerts.evaluator import DocumentScores
from app.core.enums import DocumentPriority


_PRIORITY_ORDER = {
    DocumentPriority.CRITICAL: 5,
    DocumentPriority.HIGH: 4,
    DocumentPriority.MEDIUM: 3,
    DocumentPriority.LOW: 2,
    DocumentPriority.NOISE: 1,
}


def _priority_rank(scores: DocumentScores) -> int:
    return _PRIORITY_ORDER.get(scores.recommended_priority, 0)


def _composite_score(scores: DocumentScores) -> float:
    return (
        scores.impact_score * 0.4
        + scores.relevance_score * 0.3
        + scores.novelty_score * 0.15
        + scores.credibility_score * 0.15
    )


class DigestBuilder:
    """
    Builds a ranked digest list from a collection of DocumentScores.

    Args:
        min_impact:      Minimum impact score to include (default: 0.25)
        min_relevance:   Minimum relevance score (default: 0.25)
        max_spam:        Maximum spam probability (default: 0.5)
        max_items:       Maximum items in digest (default: 20)
        min_priority:    Minimum priority to include (default: LOW)
        deduplicate:     Skip near-duplicate titles (default: True)
    """

    def __init__(
        self,
        min_impact: float = 0.25,
        min_relevance: float = 0.25,
        max_spam: float = 0.50,
        max_items: int = 20,
        min_priority: DocumentPriority = DocumentPriority.LOW,
        deduplicate: bool = True,
    ) -> None:
        self._min_impact = min_impact
        self._min_relevance = min_relevance
        self._max_spam = max_spam
        self._max_items = max_items
        self._min_priority = min_priority
        self._deduplicate = deduplicate

    def build(self, all_scores: list[DocumentScores]) -> list[DocumentScores]:
        """
        Filter, deduplicate, and rank documents for digest.
        Returns sorted list (highest priority first, then by composite score).
        """
        filtered = self._filter(all_scores)
        if self._deduplicate:
            filtered = self._dedup_titles(filtered)
        return self._rank(filtered)[: self._max_items]

    def _filter(self, items: list[DocumentScores]) -> list[DocumentScores]:
        result = []
        min_priority_rank = _PRIORITY_ORDER.get(self._min_priority, 0)
        for scores in items:
            if scores.impact_score < self._min_impact:
                continue
            if scores.relevance_score < self._min_relevance:
                continue
            if scores.spam_probability > self._max_spam:
                continue
            if _PRIORITY_ORDER.get(scores.recommended_priority, 0) < min_priority_rank:
                continue
            result.append(scores)
        return result

    def _dedup_titles(self, items: list[DocumentScores]) -> list[DocumentScores]:
        """Remove near-duplicate titles using simple token overlap."""
        import re

        def tokens(title: str) -> frozenset[str]:
            return frozenset(re.sub(r"[^a-z0-9 ]", "", title.lower()).split())

        seen: list[frozenset[str]] = []
        unique: list[DocumentScores] = []
        for scores in items:
            t = tokens(scores.title)
            is_dup = any(
                len(t & s) / max(len(t | s), 1) > 0.75 for s in seen
            )
            if not is_dup:
                seen.append(t)
                unique.append(scores)
        return unique

    def _rank(self, items: list[DocumentScores]) -> list[DocumentScores]:
        return sorted(
            items,
            key=lambda s: (_priority_rank(s), _composite_score(s)),
            reverse=True,
        )
