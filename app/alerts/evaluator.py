"""
Alert Evaluator
===============
Evaluates whether a document should trigger alerts based on AlertRules.

The evaluator is pure logic — no IO, no DB, no API calls.
It takes a document's scores and returns AlertDecision objects.

Usage:
    evaluator = AlertEvaluator(rules=DEFAULT_RULES)
    decisions = evaluator.evaluate(doc_scores)
    for decision in decisions:
        if decision.should_alert:
            await dispatcher.dispatch(decision)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.alerts.rules import AlertRule, priority_meets_threshold
from app.core.enums import AlertChannel, AlertType, DocumentPriority, SentimentLabel
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DocumentScores:
    """
    Aggregated scores for alert evaluation.
    Combines analysis result + keyword match signals.
    """
    document_id: str
    source_id: str
    source_type: str = ""
    title: str = ""
    url: str = ""
    published_at: datetime | None = None

    # Analysis scores
    sentiment_label: str = "neutral"
    sentiment_score: float = 0.0
    relevance_score: float = 0.0
    impact_score: float = 0.0
    confidence_score: float = 0.0
    novelty_score: float = 1.0
    credibility_score: float = 0.5
    spam_probability: float = 0.0
    recommended_priority: DocumentPriority = DocumentPriority.NOISE
    actionable: bool = False

    # Keyword/entity signals
    keyword_score: float = 0.0
    has_entity_hit: bool = False
    matched_entities: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)

    # Source quality
    source_credibility: float = 0.5

    # Engagement
    views: int = 0
    clicks: int = 0

    # LLM fields (for message formatting)
    explanation_short: str = ""
    affected_assets: list[str] = field(default_factory=list)
    affected_sectors: list[str] = field(default_factory=list)
    event_type: str = "unknown"
    bull_case: str = ""
    bear_case: str = ""
    tags: list[str] = field(default_factory=list)
    analyzed_by: str = ""


@dataclass
class AlertDecision:
    """Result of evaluating a document against a single AlertRule."""
    rule_name: str
    alert_type: AlertType
    channels: list[AlertChannel]
    should_alert: bool
    severity: DocumentPriority
    reasons: list[str] = field(default_factory=list)
    failed_conditions: list[str] = field(default_factory=list)
    document_scores: DocumentScores | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "alert_type": self.alert_type.value,
            "channels": [c.value for c in self.channels],
            "should_alert": self.should_alert,
            "severity": self.severity.value,
            "reasons": self.reasons,
            "failed_conditions": self.failed_conditions,
        }


def _abs_sentiment(score: float) -> float:
    return abs(score)


class AlertEvaluator:
    """
    Applies alert rules to document scores.

    Rules are evaluated independently — one document can trigger multiple rules.
    Digest rules are skipped unless include_digest=True (digest runs on schedule, not per-doc).
    """

    def __init__(self, rules: list[AlertRule] | None = None) -> None:
        from app.alerts.rules import DEFAULT_RULES
        self._rules = [r for r in (rules if rules is not None else DEFAULT_RULES) if r.enabled]

    def evaluate(
        self,
        scores: DocumentScores,
        include_digest: bool = False,
    ) -> list[AlertDecision]:
        """
        Evaluate all rules against the given scores.
        Returns one AlertDecision per rule (may have should_alert=False).
        Only returns triggering decisions (should_alert=True) for non-digest rules.
        Digest rules are skipped by default.
        """
        decisions: list[AlertDecision] = []

        for rule in self._rules:
            # Digest/daily_brief rules are triggered by schedule, not per-document
            if rule.alert_type in (AlertType.DIGEST, AlertType.DAILY_BRIEF) and not include_digest:
                continue

            decision = self._evaluate_rule(scores, rule)
            if decision.should_alert:
                decisions.append(decision)
                logger.info(
                    "alert_triggered",
                    rule=rule.name,
                    doc_id=scores.document_id,
                    alert_type=rule.alert_type.value,
                    reasons=decision.reasons,
                )

        return decisions

    def evaluate_all(self, scores: DocumentScores) -> list[AlertDecision]:
        """Evaluate all rules including digest — for preview/debug purposes."""
        return [self._evaluate_rule(scores, rule) for rule in self._rules]

    def _evaluate_rule(self, scores: DocumentScores, rule: AlertRule) -> AlertDecision:
        """Evaluate a single rule. Returns AlertDecision with full pass/fail details."""
        reasons: list[str] = []
        failed: list[str] = []

        def check(condition: bool, pass_msg: str, fail_msg: str) -> bool:
            if condition:
                reasons.append(pass_msg)
            else:
                failed.append(fail_msg)
            return condition

        passes = True

        # --- Score thresholds ---
        passes &= check(
            scores.impact_score >= rule.min_impact,
            f"impact={scores.impact_score:.2f} ≥ {rule.min_impact}",
            f"impact={scores.impact_score:.2f} < {rule.min_impact}",
        )
        passes &= check(
            scores.relevance_score >= rule.min_relevance,
            f"relevance={scores.relevance_score:.2f} ≥ {rule.min_relevance}",
            f"relevance={scores.relevance_score:.2f} < {rule.min_relevance}",
        )
        passes &= check(
            scores.credibility_score >= rule.min_credibility,
            f"credibility={scores.credibility_score:.2f} ≥ {rule.min_credibility}",
            f"credibility={scores.credibility_score:.2f} < {rule.min_credibility}",
        )
        passes &= check(
            scores.novelty_score >= rule.min_novelty,
            f"novelty={scores.novelty_score:.2f} ≥ {rule.min_novelty}",
            f"novelty={scores.novelty_score:.2f} < {rule.min_novelty}",
        )
        passes &= check(
            _abs_sentiment(scores.sentiment_score) >= rule.min_sentiment_abs,
            f"|sentiment|={_abs_sentiment(scores.sentiment_score):.2f} ≥ {rule.min_sentiment_abs}",
            f"|sentiment|={_abs_sentiment(scores.sentiment_score):.2f} < {rule.min_sentiment_abs}",
        )
        passes &= check(
            scores.keyword_score >= rule.min_keyword_score,
            f"keyword={scores.keyword_score:.2f} ≥ {rule.min_keyword_score}",
            f"keyword={scores.keyword_score:.2f} < {rule.min_keyword_score}",
        )
        passes &= check(
            scores.spam_probability <= rule.max_spam_probability,
            f"spam={scores.spam_probability:.2f} ≤ {rule.max_spam_probability}",
            f"spam={scores.spam_probability:.2f} > {rule.max_spam_probability}",
        )

        # --- Engagement ---
        if rule.min_views > 0:
            passes &= check(
                scores.views >= rule.min_views,
                f"views={scores.views} ≥ {rule.min_views}",
                f"views={scores.views} < {rule.min_views}",
            )
        if rule.min_clicks > 0:
            passes &= check(
                scores.clicks >= rule.min_clicks,
                f"clicks={scores.clicks} ≥ {rule.min_clicks}",
                f"clicks={scores.clicks} < {rule.min_clicks}",
            )

        # --- Structural conditions ---
        if rule.requires_entity_hit:
            passes &= check(
                scores.has_entity_hit,
                "entity hit confirmed",
                "no entity hit (required)",
            )
        if rule.requires_actionable:
            passes &= check(
                scores.actionable,
                "document is actionable",
                "document not actionable (required)",
            )
        passes &= check(
            priority_meets_threshold(scores.recommended_priority, rule.priority_threshold),
            f"priority={scores.recommended_priority.value} ≥ {rule.priority_threshold.value}",
            f"priority={scores.recommended_priority.value} < {rule.priority_threshold.value}",
        )

        # --- Source type filter ---
        if rule.allowed_source_types:
            from app.core.enums import SourceType
            try:
                st = SourceType(scores.source_type)
                passes &= check(
                    st in rule.allowed_source_types,
                    f"source_type={scores.source_type} allowed",
                    f"source_type={scores.source_type} not in allowed list",
                )
            except ValueError:
                passes = False
                failed.append(f"unknown source_type={scores.source_type}")

        if rule.min_source_credibility > 0:
            passes &= check(
                scores.source_credibility >= rule.min_source_credibility,
                f"source_credibility={scores.source_credibility:.2f} ≥ {rule.min_source_credibility}",
                f"source_credibility={scores.source_credibility:.2f} < {rule.min_source_credibility}",
            )

        return AlertDecision(
            rule_name=rule.name,
            alert_type=rule.alert_type,
            channels=rule.channels,
            should_alert=passes,
            severity=scores.recommended_priority,
            reasons=reasons if passes else [],
            failed_conditions=failed if not passes else [],
            document_scores=scores if passes else None,
        )
