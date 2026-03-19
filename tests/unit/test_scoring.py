"""Tests for the Priority Scoring system."""

from uuid import uuid4

from app.analysis.scoring import compute_priority, is_alert_worthy
from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel


def _make_result(**overrides) -> AnalysisResult:
    defaults = {
        "document_id": str(uuid4()),
        "sentiment_label": SentimentLabel.NEUTRAL,
        "sentiment_score": 0.0,
        "relevance_score": 0.5,
        "impact_score": 0.5,
        "confidence_score": 0.8,
        "novelty_score": 0.5,
        "actionable": False,
        "explanation_short": "Test",
        "explanation_long": "Test long",
    }
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def test_priority_baseline():
    result = _make_result(
        relevance_score=0.5,
        impact_score=0.5,
        novelty_score=0.5,
        actionable=False,
    )
    # 0.5*0.3 + 0.5*0.3 + 0.5*0.2 + 0.0 + 1.0*0.05 = 0.15 + 0.15 + 0.10 + 0 + 0.05 = 0.45
    # priority = max(1, min(10, round(0.45 * 9) + 1)) = round(4.05) + 1 = 4 + 1 = 5
    score = compute_priority(result)
    assert score.priority == 5
    assert not score.is_spam_capped
    assert not score.actionable_bonus_applied


def test_actionability_bonus():
    # Start with same base, but actionable
    result = _make_result(
        relevance_score=0.5,
        impact_score=0.5,
        novelty_score=0.5,
        actionable=True,
    )
    # raw = 0.45 + 0.15 (actionable weight) = 0.60
    # pri = round(0.60 * 9) + 1 = 5 + 1 = 6
    # bonus adds +1 => 7
    score = compute_priority(result)
    assert score.priority == 7
    assert score.actionable_bonus_applied


def test_spam_cap():
    # Very high relevance, but high spam
    result = _make_result(
        relevance_score=1.0,
        impact_score=1.0,
        novelty_score=1.0,
        actionable=True,
    )
    score = compute_priority(result, spam_probability=0.99)
    assert score.priority == 3
    assert score.is_spam_capped
    assert not is_alert_worthy(result, min_priority=1, spam_probability=0.99)


def test_perfect_score():
    result = _make_result(
        relevance_score=1.0,
        impact_score=1.0,
        novelty_score=1.0,
        actionable=True,
    )  # raw = 0.3 + 0.3 + 0.2 + 0.15 + 0.05 = 1.0
    # pri = round(9) + 1 = 10
    # bonus adds +1 but capped at 10.
    score = compute_priority(result)
    assert score.priority == 10
    assert is_alert_worthy(result, min_priority=10)
