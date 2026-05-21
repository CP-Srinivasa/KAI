"""Tests for the Priority Scoring system."""

from uuid import uuid4

from app.analysis.scoring import compute_priority, is_alert_worthy
from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel


def _make_result(**overrides) -> AnalysisResult:
    # Default sentiment is BULLISH — directional, so the sentiment-clarity
    # penalty (DS-20260520-NEW-1) does not fire. Tests that exercise the
    # penalty pass sentiment_label=NEUTRAL/MIXED explicitly.
    defaults = {
        "document_id": str(uuid4()),
        "sentiment_label": SentimentLabel.BULLISH,
        "sentiment_score": 0.5,
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
    assert not score.is_sentiment_penalized


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


# ── DS-20260520-NEW-1: sentiment-clarity penalty ─────────────────────────────


def test_sentiment_penalty_neutral_subtracts_two():
    # Same scores as test_perfect_score (priority would be 10), but NEUTRAL
    # sentiment should trigger the -2 clarity penalty → 8.
    result = _make_result(
        sentiment_label=SentimentLabel.NEUTRAL,
        relevance_score=1.0,
        impact_score=1.0,
        novelty_score=1.0,
        actionable=True,
    )
    score = compute_priority(result)
    assert score.priority == 8
    assert score.is_sentiment_penalized
    # actionable_bonus_applied=False here because raw mapping already hit 10
    # before the bonus could fire — that's existing behavior, not regression.
    assert not score.actionable_bonus_applied


def test_sentiment_penalty_mixed_subtracts_two():
    result = _make_result(
        sentiment_label=SentimentLabel.MIXED,
        relevance_score=1.0,
        impact_score=1.0,
        novelty_score=1.0,
        actionable=True,
    )
    score = compute_priority(result)
    assert score.priority == 8
    assert score.is_sentiment_penalized


def test_sentiment_penalty_does_not_fire_on_bullish():
    result = _make_result(
        sentiment_label=SentimentLabel.BULLISH,
        relevance_score=1.0,
        impact_score=1.0,
        novelty_score=1.0,
        actionable=True,
    )
    score = compute_priority(result)
    assert score.priority == 10
    assert not score.is_sentiment_penalized


def test_sentiment_penalty_does_not_fire_on_bearish():
    result = _make_result(
        sentiment_label=SentimentLabel.BEARISH,
        sentiment_score=-0.5,
        relevance_score=1.0,
        impact_score=1.0,
        novelty_score=1.0,
        actionable=True,
    )
    score = compute_priority(result)
    assert score.priority == 10
    assert not score.is_sentiment_penalized


def test_sentiment_penalty_floor_at_one():
    # Already-low priority + neutral → must not go below 1.
    result = _make_result(
        sentiment_label=SentimentLabel.NEUTRAL,
        relevance_score=0.0,
        impact_score=0.0,
        novelty_score=0.0,
        actionable=False,
    )
    score = compute_priority(result)
    assert score.priority == 1
    assert score.is_sentiment_penalized


def test_sentiment_penalty_spam_cap_still_binds():
    # Spam cap floor (3) wins over penalty floor (1) → spam still capped
    # at 3 regardless of sentiment penalty.
    result = _make_result(
        sentiment_label=SentimentLabel.NEUTRAL,
        relevance_score=1.0,
        impact_score=1.0,
        novelty_score=1.0,
        actionable=True,
    )
    score = compute_priority(result, spam_probability=0.99)
    assert score.priority == 3
    assert score.is_spam_capped


def test_priority_paradox_regression():
    # DS-20260520-NEW-1 regression guard: an asset-relevant neutral
    # regulatory-style news (high relevance/impact/novelty, actionable)
    # must NOT reach priority 10 anymore.
    neutral_regulatory = _make_result(
        sentiment_label=SentimentLabel.NEUTRAL,
        relevance_score=0.9,
        impact_score=0.85,
        novelty_score=0.8,
        actionable=True,
    )
    directional_marginal = _make_result(
        sentiment_label=SentimentLabel.BULLISH,
        relevance_score=0.6,
        impact_score=0.55,
        novelty_score=0.5,
        actionable=False,
    )
    neutral_score = compute_priority(neutral_regulatory)
    directional_score = compute_priority(directional_marginal)
    # Before the fix: neutral_regulatory landed at p=10, directional_marginal at p=6-7.
    # After the fix: clarity penalty pushes neutral down; both end up closer.
    assert neutral_score.priority < 10
    assert neutral_score.is_sentiment_penalized
