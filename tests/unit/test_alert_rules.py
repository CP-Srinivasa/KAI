"""Tests for app/alerts/rules.py"""
from __future__ import annotations

import pytest

from app.alerts.rules import (
    AlertRule,
    BREAKING_ALERT_RULE,
    WATCHLIST_HIT_RULE,
    DAILY_DIGEST_RULE,
    DEFAULT_RULES,
    priority_meets_threshold,
)
from app.core.enums import AlertChannel, AlertType, DocumentPriority, SourceType


class TestPriorityMeetsThreshold:
    def test_critical_meets_all(self) -> None:
        assert priority_meets_threshold(DocumentPriority.CRITICAL, DocumentPriority.NOISE)
        assert priority_meets_threshold(DocumentPriority.CRITICAL, DocumentPriority.LOW)
        assert priority_meets_threshold(DocumentPriority.CRITICAL, DocumentPriority.CRITICAL)

    def test_noise_meets_only_noise(self) -> None:
        assert priority_meets_threshold(DocumentPriority.NOISE, DocumentPriority.NOISE)
        assert not priority_meets_threshold(DocumentPriority.NOISE, DocumentPriority.LOW)
        assert not priority_meets_threshold(DocumentPriority.NOISE, DocumentPriority.HIGH)

    def test_high_does_not_meet_critical(self) -> None:
        assert not priority_meets_threshold(DocumentPriority.HIGH, DocumentPriority.CRITICAL)

    def test_medium_meets_low_and_medium(self) -> None:
        assert priority_meets_threshold(DocumentPriority.MEDIUM, DocumentPriority.LOW)
        assert priority_meets_threshold(DocumentPriority.MEDIUM, DocumentPriority.MEDIUM)
        assert not priority_meets_threshold(DocumentPriority.MEDIUM, DocumentPriority.HIGH)


class TestAlertRuleDefaults:
    def test_default_fields(self) -> None:
        rule = AlertRule(name="test")
        assert rule.alert_type == AlertType.BREAKING
        assert rule.channels == [AlertChannel.TELEGRAM]
        assert rule.enabled is True
        assert rule.min_impact == 0.0
        assert rule.max_spam_probability == 1.0

    def test_to_dict_contains_all_fields(self) -> None:
        rule = AlertRule(name="test", min_impact=0.7, channels=[AlertChannel.EMAIL])
        d = rule.to_dict()
        assert d["name"] == "test"
        assert d["min_impact"] == 0.7
        assert d["channels"] == ["email"]
        assert "priority_threshold" in d
        assert "dedup_window_hours" in d

    def test_custom_channels(self) -> None:
        rule = AlertRule(
            name="multi",
            channels=[AlertChannel.TELEGRAM, AlertChannel.EMAIL],
        )
        assert len(rule.channels) == 2


class TestDefaultRules:
    def test_breaking_rule_has_high_thresholds(self) -> None:
        assert BREAKING_ALERT_RULE.min_impact >= 0.70
        assert BREAKING_ALERT_RULE.min_relevance >= 0.50
        assert BREAKING_ALERT_RULE.requires_entity_hit is True

    def test_watchlist_rule_entity_required(self) -> None:
        assert WATCHLIST_HIT_RULE.requires_entity_hit is True
        assert WATCHLIST_HIT_RULE.alert_type == AlertType.WATCHLIST_HIT

    def test_digest_rule_lower_thresholds(self) -> None:
        # Digest should be more permissive than breaking
        assert DAILY_DIGEST_RULE.min_impact <= BREAKING_ALERT_RULE.min_impact
        assert DAILY_DIGEST_RULE.alert_type == AlertType.DAILY_BRIEF

    def test_all_default_rules_enabled(self) -> None:
        assert all(r.enabled for r in DEFAULT_RULES)

    def test_default_rules_not_empty(self) -> None:
        assert len(DEFAULT_RULES) >= 2
