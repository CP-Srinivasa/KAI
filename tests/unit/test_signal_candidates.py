"""Tests for signal candidate generation and ranking."""
from __future__ import annotations

import pytest

from app.alerts.evaluator import DocumentScores
from app.core.enums import (
    DirectionHint,
    DocumentPriority,
    NarrativeLabel,
    SignalUrgency,
)
from app.trading.signals.candidate import SignalCandidate
from app.trading.signals.generator import (
    GeneratorConfig,
    SignalCandidateGenerator,
    _direction_from_sentiment,
    _narrative_from_context,
    _urgency_from_priority,
)
from app.analysis.ranking.trading_ranker import RankingWeights, TradingRelevanceRanker


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

def _scores(
    title: str = "Bitcoin ETF approved",
    sentiment: str = "positive",
    sentiment_score: float = 0.80,
    impact: float = 0.85,
    relevance: float = 0.80,
    credibility: float = 0.85,
    priority: DocumentPriority = DocumentPriority.HIGH,
    affected_assets: list[str] | None = None,
    matched_entities: list[str] | None = None,
    bull_case: str = "Institutional adoption accelerates.",
    bear_case: str = "Profit-taking possible.",
) -> DocumentScores:
    return DocumentScores(
        document_id="test-doc",
        source_id="coindesk",
        title=title,
        sentiment_label=sentiment,
        sentiment_score=sentiment_score,
        impact_score=impact,
        relevance_score=relevance,
        credibility_score=credibility,
        recommended_priority=priority,
        affected_assets=affected_assets or ["BTC"],
        matched_entities=matched_entities or ["Bitcoin"],
        bull_case=bull_case,
        bear_case=bear_case,
        spam_probability=0.02,
        novelty_score=0.90,
    )


@pytest.fixture
def generator() -> SignalCandidateGenerator:
    return SignalCandidateGenerator()


@pytest.fixture
def generator_with_watchlist() -> SignalCandidateGenerator:
    from app.trading.watchlists.watchlist import WatchlistRegistry
    data = {
        "crypto": [
            {"symbol": "BTC", "name": "Bitcoin", "aliases": ["bitcoin", "btc"], "tags": ["major"]},
            {"symbol": "ETH", "name": "Ethereum", "aliases": ["ethereum"], "tags": ["defi"]},
        ],
    }
    registry = WatchlistRegistry.from_dict(data)
    return SignalCandidateGenerator(watchlist=registry)


# ──────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────

class TestHelpers:
    def test_direction_positive_high(self) -> None:
        assert _direction_from_sentiment("positive", 0.8) == DirectionHint.BULLISH

    def test_direction_negative_low(self) -> None:
        assert _direction_from_sentiment("negative", -0.7) == DirectionHint.BEARISH

    def test_direction_neutral(self) -> None:
        assert _direction_from_sentiment("neutral", 0.05) == DirectionHint.NEUTRAL

    def test_urgency_critical_breaking(self) -> None:
        from app.core.enums import AlertType
        urgency = _urgency_from_priority(DocumentPriority.CRITICAL, AlertType.BREAKING)
        assert urgency == SignalUrgency.IMMEDIATE

    def test_urgency_noise(self) -> None:
        urgency = _urgency_from_priority(DocumentPriority.NOISE, None)
        assert urgency == SignalUrgency.MONITOR

    def test_narrative_regulatory(self) -> None:
        label = _narrative_from_context(
            entities=["SEC"], tags=["regulatory"], event_type="regulatory", title="SEC sues exchange"
        )
        assert label == NarrativeLabel.REGULATORY_RISK

    def test_narrative_hack(self) -> None:
        label = _narrative_from_context(
            entities=[], tags=["hack"], event_type=None, title="Protocol hacked for $100M"
        )
        assert label == NarrativeLabel.HACK_EXPLOIT

    def test_narrative_institutional(self) -> None:
        label = _narrative_from_context(
            entities=["BlackRock"], tags=["institutional"], event_type=None, title="ETF approved"
        )
        assert label == NarrativeLabel.INSTITUTIONAL_ADOPTION


# ──────────────────────────────────────────────
# SignalCandidate
# ──────────────────────────────────────────────

class TestSignalCandidate:
    def test_to_dict_has_all_fields(self) -> None:
        c = SignalCandidate(asset="BTC", confidence=0.75)
        d = c.to_dict()
        required = [
            "id", "asset", "direction_hint", "confidence", "urgency",
            "narrative_label", "recommended_next_step",
            "supporting_evidence", "contradicting_evidence", "risk_notes",
        ]
        for field in required:
            assert field in d

    def test_is_high_confidence(self) -> None:
        assert SignalCandidate(asset="BTC", confidence=0.75).is_high_confidence
        assert not SignalCandidate(asset="BTC", confidence=0.65).is_high_confidence

    def test_is_actionable(self) -> None:
        c = SignalCandidate(
            asset="BTC",
            confidence=0.60,
            urgency=SignalUrgency.SHORT_TERM,
        )
        assert c.is_actionable

    def test_monitor_urgency_not_actionable(self) -> None:
        c = SignalCandidate(
            asset="BTC",
            confidence=0.80,
            urgency=SignalUrgency.MONITOR,
        )
        assert not c.is_actionable


# ──────────────────────────────────────────────
# Generator
# ──────────────────────────────────────────────

class TestSignalCandidateGenerator:
    def test_generates_candidates_for_btc(self, generator: SignalCandidateGenerator) -> None:
        candidates = generator.generate(_scores())
        assert len(candidates) > 0
        assert any(c.asset == "BTC" for c in candidates)

    def test_low_impact_skipped(self, generator: SignalCandidateGenerator) -> None:
        scores = _scores(impact=0.05)
        candidates = generator.generate(scores)
        assert len(candidates) == 0

    def test_bull_case_in_supporting_evidence(self, generator: SignalCandidateGenerator) -> None:
        candidates = generator.generate(_scores(bull_case="ETF adoption accelerates."))
        btc_c = next((c for c in candidates if c.asset == "BTC"), None)
        assert btc_c is not None
        assert any("ETF adoption accelerates" in e for e in btc_c.supporting_evidence)

    def test_bear_case_in_contradicting_evidence(self, generator: SignalCandidateGenerator) -> None:
        candidates = generator.generate(_scores(bear_case="Regulatory crackdown possible."))
        btc_c = next((c for c in candidates if c.asset == "BTC"), None)
        assert btc_c is not None
        assert any("Regulatory crackdown" in e for e in btc_c.contradicting_evidence)

    def test_low_credibility_adds_risk_note(self, generator: SignalCandidateGenerator) -> None:
        candidates = generator.generate(_scores(credibility=0.30))
        assert len(candidates) > 0
        btc = next((c for c in candidates if c.asset == "BTC"), None)
        assert btc is not None
        assert any("credibility" in r.lower() for r in btc.risk_notes)

    def test_negative_sentiment_bearish(self, generator: SignalCandidateGenerator) -> None:
        candidates = generator.generate(_scores(sentiment="negative", sentiment_score=-0.75))
        assert all(c.direction_hint == DirectionHint.BEARISH for c in candidates)

    def test_max_assets_enforced(self) -> None:
        config = GeneratorConfig(max_assets_per_doc=2, min_impact_score=0.0)
        gen = SignalCandidateGenerator(config=config)
        candidates = gen.generate(_scores(
            affected_assets=["BTC", "ETH", "SOL", "BNB"],
            matched_entities=[],
        ))
        assert len(candidates) <= 2

    def test_generates_recommended_next_step(self, generator: SignalCandidateGenerator) -> None:
        candidates = generator.generate(_scores())
        assert all(c.recommended_next_step for c in candidates)

    def test_watchlist_enrichment(self, generator_with_watchlist: SignalCandidateGenerator) -> None:
        scores = _scores(title="Bitcoin ETF surges", affected_assets=[], matched_entities=[])
        candidates = generator_with_watchlist.generate(scores)
        assets = [c.asset for c in candidates]
        assert "BTC" in assets


# ──────────────────────────────────────────────
# TradingRelevanceRanker
# ──────────────────────────────────────────────

class TestTradingRelevanceRanker:
    def _make_candidate(self, confidence: float, urgency: SignalUrgency, impact: float) -> SignalCandidate:
        c = SignalCandidate(asset="BTC", confidence=confidence)
        c.urgency = urgency
        c.impact_score = impact
        c.source_quality = 0.8
        return c

    def test_higher_confidence_ranks_higher(self) -> None:
        ranker = TradingRelevanceRanker()
        low = self._make_candidate(0.40, SignalUrgency.MONITOR, 0.40)
        high = self._make_candidate(0.90, SignalUrgency.IMMEDIATE, 0.90)
        ranked = ranker.rank([low, high])
        assert ranked[0][0].confidence == 0.90

    def test_sorted_descending(self) -> None:
        ranker = TradingRelevanceRanker()
        candidates = [
            self._make_candidate(0.50, SignalUrgency.MONITOR, 0.50),
            self._make_candidate(0.90, SignalUrgency.IMMEDIATE, 0.90),
            self._make_candidate(0.70, SignalUrgency.SHORT_TERM, 0.70),
        ]
        ranked = ranker.rank(candidates)
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_top_n_respects_limit(self) -> None:
        ranker = TradingRelevanceRanker()
        candidates = [
            self._make_candidate(0.80 - i * 0.05, SignalUrgency.SHORT_TERM, 0.80)
            for i in range(10)
        ]
        result = ranker.top_n(candidates, n=3)
        assert len(result) <= 3

    def test_top_n_min_score_filter(self) -> None:
        ranker = TradingRelevanceRanker()
        low = self._make_candidate(0.10, SignalUrgency.MONITOR, 0.10)
        result = ranker.top_n([low], min_score=0.80)
        assert len(result) == 0

    def test_empty_input(self) -> None:
        ranker = TradingRelevanceRanker()
        assert ranker.rank([]) == []
        assert ranker.top_n([]) == []
