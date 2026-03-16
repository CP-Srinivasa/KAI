"""Tests for app/alerts/evaluator.py"""
from __future__ import annotations

import pytest

from app.alerts.evaluator import AlertDecision, AlertEvaluator, DocumentScores
from app.alerts.rules import AlertRule, DEFAULT_RULES
from app.core.enums import AlertChannel, AlertType, DocumentPriority, SourceType


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

def _passing_scores() -> DocumentScores:
    """Scores that should pass the BREAKING_ALERT_RULE."""
    return DocumentScores(
        document_id="doc-pass-001",
        source_id="coindesk_rss",
        source_type="rss_feed",
        title="Bitcoin ETF approved by SEC",
        sentiment_label="positive",
        sentiment_score=0.82,
        relevance_score=0.85,
        impact_score=0.90,
        confidence_score=0.78,
        novelty_score=0.95,
        credibility_score=0.75,
        spam_probability=0.02,
        recommended_priority=DocumentPriority.CRITICAL,
        actionable=True,
        keyword_score=0.80,
        has_entity_hit=True,
        matched_entities=["Bitcoin"],
        source_credibility=0.80,
    )


def _failing_scores() -> DocumentScores:
    """Scores that should fail most rules (low everything)."""
    return DocumentScores(
        document_id="doc-fail-001",
        source_id="unknown",
        source_type="website",
        title="Generic news article",
        sentiment_label="neutral",
        sentiment_score=0.05,
        relevance_score=0.10,
        impact_score=0.10,
        novelty_score=0.20,
        credibility_score=0.30,
        spam_probability=0.70,
        recommended_priority=DocumentPriority.NOISE,
        actionable=False,
        keyword_score=0.05,
        has_entity_hit=False,
    )


def _make_simple_rule(name: str = "test_rule", **kwargs) -> AlertRule:
    return AlertRule(
        name=name,
        alert_type=AlertType.BREAKING,
        channels=[AlertChannel.TELEGRAM],
        **kwargs,
    )


# ──────────────────────────────────────────────
# DocumentScores
# ──────────────────────────────────────────────

class TestDocumentScores:
    def test_defaults(self) -> None:
        scores = DocumentScores(document_id="x", source_id="y")
        assert scores.sentiment_label == "neutral"
        assert scores.novelty_score == 1.0
        assert scores.credibility_score == 0.5
        assert scores.has_entity_hit is False

    def test_matched_entities_default_empty(self) -> None:
        scores = DocumentScores(document_id="x", source_id="y")
        assert scores.matched_entities == []


# ──────────────────────────────────────────────
# AlertEvaluator — single rule
# ──────────────────────────────────────────────

class TestAlertEvaluator:
    def test_passes_when_all_above_threshold(self) -> None:
        rule = _make_simple_rule(min_impact=0.5, min_relevance=0.5)
        evaluator = AlertEvaluator(rules=[rule])
        scores = _passing_scores()
        decisions = evaluator.evaluate(scores)
        assert len(decisions) == 1
        assert decisions[0].should_alert is True

    def test_fails_when_impact_below_threshold(self) -> None:
        rule = _make_simple_rule(min_impact=0.95)
        evaluator = AlertEvaluator(rules=[rule])
        scores = _passing_scores()
        decisions = evaluator.evaluate(scores)
        assert len(decisions) == 0  # No triggering decisions

    def test_fails_when_spam_too_high(self) -> None:
        rule = _make_simple_rule(max_spam_probability=0.10)
        evaluator = AlertEvaluator(rules=[rule])
        scores = _passing_scores()
        scores.spam_probability = 0.50
        decisions = evaluator.evaluate(scores)
        assert len(decisions) == 0

    def test_fails_when_entity_hit_required_but_missing(self) -> None:
        rule = _make_simple_rule(requires_entity_hit=True)
        evaluator = AlertEvaluator(rules=[rule])
        scores = _passing_scores()
        scores.has_entity_hit = False
        decisions = evaluator.evaluate(scores)
        assert len(decisions) == 0

    def test_fails_priority_threshold(self) -> None:
        rule = _make_simple_rule(priority_threshold=DocumentPriority.CRITICAL)
        evaluator = AlertEvaluator(rules=[rule])
        scores = _passing_scores()
        scores.recommended_priority = DocumentPriority.LOW
        decisions = evaluator.evaluate(scores)
        assert len(decisions) == 0

    def test_sentiment_abs_check(self) -> None:
        rule = _make_simple_rule(min_sentiment_abs=0.70)
        evaluator = AlertEvaluator(rules=[rule])

        # Positive 0.82 → passes
        scores = _passing_scores()
        scores.sentiment_score = 0.82
        assert len(evaluator.evaluate(scores)) == 1

        # Negative -0.82 → also passes (abs value)
        scores.sentiment_score = -0.82
        assert len(evaluator.evaluate(scores)) == 1

        # Near-zero → fails
        scores.sentiment_score = 0.10
        assert len(evaluator.evaluate(scores)) == 0

    def test_keyword_score_threshold(self) -> None:
        rule = _make_simple_rule(min_keyword_score=0.5)
        evaluator = AlertEvaluator(rules=[rule])
        scores = _passing_scores()
        scores.keyword_score = 0.3
        assert len(evaluator.evaluate(scores)) == 0
        scores.keyword_score = 0.7
        assert len(evaluator.evaluate(scores)) == 1

    def test_multiple_rules_independent(self) -> None:
        rule1 = AlertRule(name="r1", min_impact=0.5, alert_type=AlertType.BREAKING, channels=[AlertChannel.TELEGRAM])
        rule2 = AlertRule(name="r2", min_impact=0.95, alert_type=AlertType.WATCHLIST_HIT, channels=[AlertChannel.EMAIL])
        evaluator = AlertEvaluator(rules=[rule1, rule2])
        scores = _passing_scores()
        decisions = evaluator.evaluate(scores)
        # Only r1 should trigger (impact=0.90 < 0.95 for r2)
        assert len(decisions) == 1
        assert decisions[0].rule_name == "r1"

    def test_digest_rules_skipped_by_default(self) -> None:
        digest_rule = AlertRule(
            name="digest",
            alert_type=AlertType.DAILY_BRIEF,
            channels=[AlertChannel.EMAIL],
        )
        evaluator = AlertEvaluator(rules=[digest_rule])
        scores = _passing_scores()
        # Should not trigger by default (digest runs on schedule)
        decisions = evaluator.evaluate(scores, include_digest=False)
        assert len(decisions) == 0

    def test_digest_rules_included_when_flag_set(self) -> None:
        digest_rule = AlertRule(
            name="digest",
            alert_type=AlertType.DAILY_BRIEF,
            channels=[AlertChannel.EMAIL],
            min_impact=0.0,
        )
        evaluator = AlertEvaluator(rules=[digest_rule])
        scores = _passing_scores()
        decisions = evaluator.evaluate(scores, include_digest=True)
        assert len(decisions) == 1

    def test_evaluate_all_returns_all_decisions(self) -> None:
        rules = [
            _make_simple_rule(name="pass_rule", min_impact=0.5),
            _make_simple_rule(name="fail_rule", min_impact=0.99),
        ]
        evaluator = AlertEvaluator(rules=rules)
        decisions = evaluator.evaluate_all(_passing_scores())
        assert len(decisions) == 2
        names = {d.rule_name for d in decisions}
        assert "pass_rule" in names
        assert "fail_rule" in names

    def test_failed_conditions_populated_on_failure(self) -> None:
        rule = _make_simple_rule(min_impact=0.99)
        evaluator = AlertEvaluator(rules=[rule])
        decisions = evaluator.evaluate_all(_passing_scores())
        failed_decision = decisions[0]
        assert failed_decision.should_alert is False
        assert len(failed_decision.failed_conditions) > 0

    def test_passing_decision_has_reasons(self) -> None:
        rule = _make_simple_rule(min_impact=0.5)
        evaluator = AlertEvaluator(rules=[rule])
        decisions = evaluator.evaluate_all(_passing_scores())
        passing = decisions[0]
        assert passing.should_alert is True
        assert len(passing.reasons) > 0

    def test_to_dict(self) -> None:
        rule = _make_simple_rule(min_impact=0.5)
        evaluator = AlertEvaluator(rules=[rule])
        decisions = evaluator.evaluate_all(_passing_scores())
        d = decisions[0].to_dict()
        assert "rule_name" in d
        assert "should_alert" in d
        assert "channels" in d

    def test_no_rules_no_alerts(self) -> None:
        evaluator = AlertEvaluator(rules=[])
        assert evaluator.evaluate(_passing_scores()) == []

    def test_disabled_rule_not_evaluated(self) -> None:
        rule = AlertRule(name="disabled", min_impact=0.0, enabled=False)
        evaluator = AlertEvaluator(rules=[rule])
        # Disabled rules are filtered at init
        assert len(evaluator._rules) == 0

    def test_failing_scores_no_alerts_on_default_rules(self) -> None:
        evaluator = AlertEvaluator(rules=DEFAULT_RULES)
        decisions = evaluator.evaluate(_failing_scores())
        assert len(decisions) == 0

    def test_passing_scores_triggers_breaking_on_default(self) -> None:
        evaluator = AlertEvaluator(rules=DEFAULT_RULES)
        decisions = evaluator.evaluate(_passing_scores())
        alert_types = {d.alert_type for d in decisions}
        assert AlertType.BREAKING in alert_types or AlertType.WATCHLIST_HIT in alert_types
