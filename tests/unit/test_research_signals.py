"""Tests for Signal Candidates module."""

import pytest
from pydantic import ValidationError

from app.core.enums import MarketScope, SentimentLabel
from app.research.signals import SignalCandidate, extract_signal_candidates
from tests.unit.factories import make_document


def test_extract_signal_candidates_filters_priority():
    docs = [
        make_document(
            is_analyzed=True,
            priority_score=9,
            sentiment_label=SentimentLabel.BULLISH,
            tickers=["BTC"],
        ),
        make_document(
            is_analyzed=True,
            priority_score=6,  # Below threshold
            sentiment_label=SentimentLabel.BEARISH,
        ),
        make_document(
            is_analyzed=False,  # Not analyzed
            priority_score=10,
        ),
    ]
    candidates = extract_signal_candidates(docs, min_priority=8)
    assert len(candidates) == 1
    assert candidates[0].priority == 9
    assert candidates[0].sentiment == SentimentLabel.BULLISH
    assert candidates[0].action_direction == "buy"
    assert "BTC" in candidates[0].affected_assets


def test_extract_signal_candidates_direction_mapping():
    docs = [
        make_document(is_analyzed=True, priority_score=8, sentiment_label=SentimentLabel.BULLISH),
        make_document(is_analyzed=True, priority_score=8, sentiment_label=SentimentLabel.BEARISH),
        make_document(is_analyzed=True, priority_score=8, sentiment_label=SentimentLabel.NEUTRAL),
    ]
    candidates = extract_signal_candidates(docs)
    assert len(candidates) == 3
    # Order usually depends on priority, since priority is equal, stable sort preserves it
    assert candidates[0].action_direction == "buy"
    assert candidates[1].action_direction == "sell"
    assert candidates[2].action_direction == "hold"


def test_signal_candidate_strict_validation():
    # Will raise ValidationError if priority < 8 due to Field(ge=8) if we directly instantiate
    with pytest.raises(ValidationError):
        SignalCandidate(
            signal_id="123",
            document_id="doc_123",
            title="Test",
            summary="Test Summary",
            priority=5,  # Invalid
            sentiment=SentimentLabel.BULLISH,
            action_direction="buy",
            affected_assets=[],
            market_scope=MarketScope.UNKNOWN,
            published_at=None,
        )
