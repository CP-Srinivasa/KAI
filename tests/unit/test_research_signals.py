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
            relevance_score=0.9,
            spam_probability=0.01,
            credibility_score=0.99,
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
    assert candidates[0].direction_hint == "bullish"
    assert "BTC" in candidates[0].affected_assets
    assert candidates[0].confidence == 0.9
    assert candidates[0].source_quality == 0.99
    assert candidates[0].target_asset == "BTC"
    assert candidates[0].analysis_source in ("external_llm", "rule", "internal")


def test_extract_signal_candidates_direction_mapping():
    docs = [
        make_document(is_analyzed=True, priority_score=8, sentiment_label=SentimentLabel.BULLISH),
        make_document(is_analyzed=True, priority_score=8, sentiment_label=SentimentLabel.BEARISH),
        make_document(is_analyzed=True, priority_score=8, sentiment_label=SentimentLabel.NEUTRAL),
    ]
    candidates = extract_signal_candidates(docs)
    assert len(candidates) == 3
    directions = {c.direction_hint for c in candidates}
    assert directions == {"bullish", "bearish", "neutral"}


def test_signal_candidate_analysis_source_propagation():
    docs = [
        make_document(is_analyzed=True, priority_score=10, provider="openai"),
        make_document(is_analyzed=True, priority_score=10, provider="companion"),
        make_document(is_analyzed=True, priority_score=10, provider="rule"),
        make_document(is_analyzed=True, priority_score=10, provider=None),
    ]
    candidates = extract_signal_candidates(docs)
    sources = [c.analysis_source for c in candidates]

    assert sources.count("external_llm") == 1
    assert sources.count("internal") == 1
    assert sources.count("rule") == 2


def test_signal_candidate_strict_validation():
    # Will raise ValidationError if priority < 0 due to Field(ge=0)
    with pytest.raises(ValidationError):
        SignalCandidate(
            signal_id="123",
            document_id="doc-456",
            target_asset="ETH",
            direction_hint="neutral",
            confidence=0.9,
            supporting_evidence="Good news",
            contradicting_evidence="None",
            risk_notes="High vol",
            source_quality=0.8,
            recommended_next_step="Review ETH neutral signal — human decision required.",
            sentiment=SentimentLabel.BULLISH,
            affected_assets=[],
            market_scope=MarketScope.UNKNOWN,
            published_at=None,
            analysis_source="rule",
        )


def test_extract_signal_candidates_watchlist_boost():
    docs = [
        make_document(
            is_analyzed=True,
            priority_score=7,  # Fails min_priority=8 default
            sentiment_label=SentimentLabel.BULLISH,
            tickers=["DOGE"],
        )
    ]
    # Without boost -> misses
    assert len(extract_signal_candidates(docs, min_priority=8)) == 0

    # With boost of 2 -> effective priority 9 -> succeeds
    candidates = extract_signal_candidates(docs, min_priority=8, watchlist_boosts={"DOGE": 2})
    assert len(candidates) == 1
    assert candidates[0].priority == 9
    assert candidates[0].target_asset == "DOGE"


def test_extract_signal_candidates_document_id_traceability():
    doc = make_document(
        is_analyzed=True,
        priority_score=8,
        sentiment_label=SentimentLabel.BULLISH,
    )
    candidates = extract_signal_candidates([doc])
    assert len(candidates) == 1
    assert candidates[0].document_id == str(doc.id)
    assert candidates[0].signal_id == f"sig_{doc.id}"


def test_extract_signal_candidates_fallback_compatible():
    # A fallback-analyzed document might lack scores and sentiment entirely
    fallback_doc = make_document(
        is_analyzed=True,
        priority_score=None,
        sentiment_label=None,
        relevance_score=None,
        credibility_score=None,
        spam_probability=None,
        market_scope=MarketScope.UNKNOWN,
        tickers=[],
        crypto_assets=[],
    )
    # Give it a low min_priority to ensure it passes the filter
    # despite having 0 priority
    candidates = extract_signal_candidates([fallback_doc], min_priority=0)

    assert len(candidates) == 1
    sig = candidates[0]

    assert sig.priority == 0
    assert sig.direction_hint == "neutral"
    assert sig.sentiment == SentimentLabel.NEUTRAL
    assert sig.confidence == 0.5  # fallback default
    assert sig.source_quality == 0.5  # fallback default
    assert sig.target_asset == "General Market"  # fallback primary asset
    assert sig.analysis_source == "rule"
    assert "spam_prob=0.00" in sig.risk_notes
    assert "scope=unknown" in sig.risk_notes
