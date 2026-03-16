"""
Alert Rules
===========
Defines configurable threshold rules for triggering alerts.

Rules are evaluated against a document's AnalysisResult and scoring signals.
Each rule can target a specific AlertType and one or more AlertChannels.

Rules are intentionally data-only (no IO). The AlertEvaluator applies them.

Example rule definitions (YAML-loadable):
    - name: "Breaking Crypto Alert"
      alert_type: breaking
      channels: [telegram]
      min_impact: 0.80
      min_relevance: 0.65
      min_credibility: 0.50
      requires_entity_hit: true
      priority_threshold: high
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.enums import AlertChannel, AlertType, DocumentPriority, SourceType


@dataclass
class AlertRule:
    """
    A single configurable alert rule.
    All threshold fields are ANDed together — a document must satisfy ALL active conditions.
    Leave a field at its default to disable that condition.
    """
    name: str
    alert_type: AlertType = AlertType.BREAKING
    channels: list[AlertChannel] = field(default_factory=lambda: [AlertChannel.TELEGRAM])

    # Score thresholds — set to 0.0 to disable
    min_impact: float = 0.0
    min_relevance: float = 0.0
    min_credibility: float = 0.0
    min_novelty: float = 0.0
    min_sentiment_abs: float = 0.0   # |sentiment_score| ≥ this
    min_keyword_score: float = 0.0
    max_spam_probability: float = 1.0  # Reject if spam_prob > this

    # Engagement thresholds
    min_views: int = 0
    min_clicks: int = 0

    # Structural conditions
    requires_entity_hit: bool = False
    requires_actionable: bool = False
    priority_threshold: DocumentPriority = DocumentPriority.NOISE  # Minimum priority
    allowed_source_types: list[SourceType] = field(default_factory=list)  # Empty = any
    min_source_credibility: float = 0.0

    # Deduplication window (hours). 0 = no dedup.
    dedup_window_hours: int = 24

    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "alert_type": self.alert_type.value,
            "channels": [c.value for c in self.channels],
            "min_impact": self.min_impact,
            "min_relevance": self.min_relevance,
            "min_credibility": self.min_credibility,
            "min_novelty": self.min_novelty,
            "min_sentiment_abs": self.min_sentiment_abs,
            "min_keyword_score": self.min_keyword_score,
            "max_spam_probability": self.max_spam_probability,
            "requires_entity_hit": self.requires_entity_hit,
            "requires_actionable": self.requires_actionable,
            "priority_threshold": self.priority_threshold.value,
            "dedup_window_hours": self.dedup_window_hours,
            "enabled": self.enabled,
        }


# ─────────────────────────────────────────────
# Default rule sets
# ─────────────────────────────────────────────

BREAKING_ALERT_RULE = AlertRule(
    name="breaking_crypto",
    alert_type=AlertType.BREAKING,
    channels=[AlertChannel.TELEGRAM],
    min_impact=0.75,
    min_relevance=0.60,
    min_credibility=0.50,
    min_novelty=0.40,
    max_spam_probability=0.30,
    requires_entity_hit=True,
    priority_threshold=DocumentPriority.HIGH,
    dedup_window_hours=6,
)

WATCHLIST_HIT_RULE = AlertRule(
    name="watchlist_entity_hit",
    alert_type=AlertType.WATCHLIST_HIT,
    channels=[AlertChannel.TELEGRAM],
    min_impact=0.40,
    min_relevance=0.40,
    min_credibility=0.40,
    max_spam_probability=0.50,
    requires_entity_hit=True,
    priority_threshold=DocumentPriority.MEDIUM,
    dedup_window_hours=12,
)

DAILY_DIGEST_RULE = AlertRule(
    name="daily_digest",
    alert_type=AlertType.DAILY_BRIEF,
    channels=[AlertChannel.TELEGRAM, AlertChannel.EMAIL],
    min_impact=0.30,
    min_relevance=0.30,
    min_credibility=0.30,
    max_spam_probability=0.60,
    priority_threshold=DocumentPriority.LOW,
    dedup_window_hours=0,  # Digest handles its own aggregation
)

DEFAULT_RULES: list[AlertRule] = [
    BREAKING_ALERT_RULE,
    WATCHLIST_HIT_RULE,
    DAILY_DIGEST_RULE,
]


# ─────────────────────────────────────────────
# Priority ordering
# ─────────────────────────────────────────────

_PRIORITY_ORDER = {
    DocumentPriority.CRITICAL: 5,
    DocumentPriority.HIGH: 4,
    DocumentPriority.MEDIUM: 3,
    DocumentPriority.LOW: 2,
    DocumentPriority.NOISE: 1,
}


def priority_meets_threshold(
    actual: DocumentPriority,
    threshold: DocumentPriority,
) -> bool:
    """Return True if actual priority is >= threshold priority."""
    return _PRIORITY_ORDER.get(actual, 0) >= _PRIORITY_ORDER.get(threshold, 0)
