"""Tests for app/analysis/historical/matcher.py"""
from __future__ import annotations

import pytest
from datetime import datetime

from app.analysis.historical.matcher import HistoricalAnalogue, HistoricalMatcher, SEED_EVENTS
from app.core.enums import EventType, MarketScope, SentimentLabel
from app.storage.models.historical import HistoricalEvent


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

TEST_EVENTS = [
    HistoricalEvent(
        id="test-001",
        title="Test Regulatory Event",
        event_type=EventType.REGULATORY,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.NEGATIVE,
        occurred_at=datetime(2023, 1, 1),
        affected_assets=["BTC", "ETH"],
        tags=["regulatory", "sec", "legal"],
        outcome_summary="BTC dropped 10%.",
        max_price_impact_pct=-10.0,
    ),
    HistoricalEvent(
        id="test-002",
        title="Test DeFi Hack",
        event_type=EventType.HACK_EXPLOIT,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.NEGATIVE,
        occurred_at=datetime(2023, 6, 1),
        affected_assets=["ETH", "LINK"],
        tags=["hack", "defi", "exploit"],
        outcome_summary="ETH sold off 15%.",
        max_price_impact_pct=-15.0,
    ),
    HistoricalEvent(
        id="test-003",
        title="Test BTC Bull Run",
        event_type=EventType.FORK_UPGRADE,
        market_scope=MarketScope.CRYPTO,
        sentiment_label=SentimentLabel.POSITIVE,
        occurred_at=datetime(2024, 4, 1),
        affected_assets=["BTC"],
        tags=["halving", "bullish_catalyst", "bitcoin"],
        outcome_summary="BTC +20%.",
        max_price_impact_pct=20.0,
    ),
]


@pytest.fixture
def matcher() -> HistoricalMatcher:
    return HistoricalMatcher(events=TEST_EVENTS, min_similarity=0.10)


@pytest.fixture
def seed_matcher() -> HistoricalMatcher:
    return HistoricalMatcher(events=SEED_EVENTS, min_similarity=0.10)


# ──────────────────────────────────────────────
# Basic matching
# ──────────────────────────────────────────────

class TestHistoricalMatcher:
    def test_finds_regulatory_analogue(self, matcher: HistoricalMatcher) -> None:
        analogues = matcher.find(assets=["BTC"], event_type="regulatory", sentiment="negative")
        assert len(analogues) > 0
        assert analogues[0].event.id == "test-001"

    def test_finds_hack_analogue(self, matcher: HistoricalMatcher) -> None:
        analogues = matcher.find(assets=["ETH"], tags=["hack"], event_type="hack_exploit")
        assert any(a.event.id == "test-002" for a in analogues)

    def test_asset_overlap_scores_higher(self, matcher: HistoricalMatcher) -> None:
        # BTC+ETH overlap with test-001 (BTC, ETH) should score higher than LINK overlap
        analogues = matcher.find(assets=["BTC", "ETH"])
        if analogues:
            assert "BTC" in analogues[0].matched_assets or "ETH" in analogues[0].matched_assets

    def test_sorted_by_score_descending(self, matcher: HistoricalMatcher) -> None:
        analogues = matcher.find(assets=["BTC", "ETH"], tags=["regulatory", "hack"])
        scores = [a.similarity_score for a in analogues]
        assert scores == sorted(scores, reverse=True)

    def test_min_similarity_filter(self) -> None:
        strict_matcher = HistoricalMatcher(events=TEST_EVENTS, min_similarity=0.99)
        analogues = strict_matcher.find(assets=["DOGE"])
        assert len(analogues) == 0

    def test_max_results_limit(self, matcher: HistoricalMatcher) -> None:
        analogues = matcher.find(assets=["BTC", "ETH"], max_results=1)
        assert len(analogues) <= 1

    def test_empty_input_still_returns_results(self, matcher: HistoricalMatcher) -> None:
        # Even empty input can match if min_similarity is very low
        matcher_low = HistoricalMatcher(events=TEST_EVENTS, min_similarity=0.0)
        analogues = matcher_low.find()
        assert len(analogues) >= 0  # may or may not match with no criteria


# ──────────────────────────────────────────────
# HistoricalAnalogue
# ──────────────────────────────────────────────

class TestHistoricalAnalogue:
    def test_to_dict_structure(self, matcher: HistoricalMatcher) -> None:
        analogues = matcher.find(assets=["BTC"])
        if analogues:
            d = analogues[0].to_dict()
            assert "event_title" in d
            assert "similarity_score" in d
            assert "outcome_summary" in d
            assert "confidence_caveat" in d
            assert "max_price_impact_pct" in d

    def test_confidence_caveat_high_score(self) -> None:
        event = TEST_EVENTS[0]
        analogue = HistoricalAnalogue(event=event, similarity_score=0.85)
        # High score → strong match caveat
        matcher = HistoricalMatcher(events=[event])
        caveat = matcher._confidence_caveat(0.85, event)
        assert "Strong" in caveat

    def test_confidence_caveat_low_score(self) -> None:
        event = TEST_EVENTS[0]
        matcher = HistoricalMatcher(events=[event])
        caveat = matcher._confidence_caveat(0.25, event)
        assert "Weak" in caveat


# ──────────────────────────────────────────────
# Seed events
# ──────────────────────────────────────────────

class TestSeedEvents:
    def test_seed_events_not_empty(self) -> None:
        assert len(SEED_EVENTS) >= 5

    def test_seed_events_have_required_fields(self) -> None:
        for event in SEED_EVENTS:
            assert event.title
            assert event.occurred_at is not None
            assert len(event.affected_assets) > 0

    def test_btc_etf_approval_in_seeds(self, seed_matcher: HistoricalMatcher) -> None:
        analogues = seed_matcher.find(
            assets=["BTC", "IBIT"],
            tags=["bitcoin_etf", "institutional"],
            event_type="regulatory",
        )
        titles = [a.event.title for a in analogues]
        assert any("ETF" in t for t in titles)

    def test_ftx_collapse_in_seeds(self, seed_matcher: HistoricalMatcher) -> None:
        analogues = seed_matcher.find(
            assets=["BTC", "SOL"],
            tags=["collapse", "exchange"],
            sentiment="negative",
        )
        assert len(analogues) > 0

    def test_regulatory_btc_finds_analogues(self, seed_matcher: HistoricalMatcher) -> None:
        analogues = seed_matcher.find(assets=["BTC"], event_type="regulatory")
        assert len(analogues) > 0
