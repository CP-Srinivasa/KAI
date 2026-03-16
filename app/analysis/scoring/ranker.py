"""
Document Scoring & Ranking
===========================
Rule-based scoring for documents before LLM analysis.
Factors: keyword match, source credibility, recency, engagement, entity watchlist.
"""

from __future__ import annotations

import math
from datetime import datetime

from app.core.domain.document import CanonicalDocument
from app.core.logging import get_logger

logger = get_logger(__name__)


def _hours_ago(dt: datetime | None) -> float:
    if dt is None:
        return 9999.0
    delta = datetime.utcnow() - dt.replace(tzinfo=None) if dt.tzinfo else datetime.utcnow() - dt
    return max(0.0, delta.total_seconds() / 3600)


def recency_score(published_at: datetime | None, half_life_hours: float = 24.0) -> float:
    """Exponential decay. Returns 1.0 for just-published, approaches 0.0 for old."""
    return math.exp(-_hours_ago(published_at) / half_life_hours)


def keyword_match_score(text: str, keywords: list[str]) -> float:
    """Fraction of keywords found in text, capped at 1.0."""
    if not keywords or not text:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in text_lower)
    return min(1.0, hits / max(1, len(keywords)))


def engagement_score(views: int, clicks: int, max_views: int = 100000) -> float:
    """Log-scaled engagement score."""
    total = views + clicks * 2
    if total <= 0:
        return 0.0
    return min(1.0, math.log1p(total) / math.log1p(max_views))


class DocumentScorer:
    """Computes composite priority score for documents."""

    def __init__(
        self,
        weight_recency: float = 0.25,
        weight_credibility: float = 0.25,
        weight_keyword: float = 0.25,
        weight_engagement: float = 0.15,
        weight_entity_hit: float = 0.10,
        recency_half_life_hours: float = 24.0,
        watched_keywords: list[str] | None = None,
        watched_entities: list[str] | None = None,
    ) -> None:
        self.weight_recency = weight_recency
        self.weight_credibility = weight_credibility
        self.weight_keyword = weight_keyword
        self.weight_engagement = weight_engagement
        self.weight_entity_hit = weight_entity_hit
        self.recency_half_life_hours = recency_half_life_hours
        self.watched_keywords = watched_keywords or []
        self.watched_entities = watched_entities or []

    def score(self, document: CanonicalDocument, source_credibility: float = 0.5) -> float:
        text = f"{document.title} {document.cleaned_text or document.summary}"
        s_recency = recency_score(document.published_at, self.recency_half_life_hours)
        s_keyword = keyword_match_score(text, self.watched_keywords)
        s_engagement = engagement_score(document.views, document.clicks)
        entity_names = {e.name.lower() for e in document.entities}
        s_entity = 1.0 if any(e.lower() in entity_names for e in self.watched_entities) else 0.0

        result = (
            self.weight_recency * s_recency
            + self.weight_credibility * source_credibility
            + self.weight_keyword * s_keyword
            + self.weight_engagement * s_engagement
            + self.weight_entity_hit * s_entity
        )
        return round(min(1.0, result), 4)

    def rank(
        self,
        documents: list[CanonicalDocument],
        source_credibility_map: dict[str, float] | None = None,
    ) -> list[tuple[CanonicalDocument, float]]:
        cred_map = source_credibility_map or {}
        scored = [
            (doc, self.score(doc, source_credibility=cred_map.get(doc.source_id, 0.5)))
            for doc in documents
        ]
        return sorted(scored, key=lambda x: x[1], reverse=True)
