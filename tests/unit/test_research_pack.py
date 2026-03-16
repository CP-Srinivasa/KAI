"""Tests for research pack models and builder."""
from __future__ import annotations

import pytest

from app.core.enums import DirectionHint, NarrativeLabel, SignalUrgency
from app.trading.signals.candidate import SignalCandidate
from app.research.builder import ResearchPackBuilder
from app.research.models import (
    AssetResearchPack,
    BreakingNewsPack,
    DailyResearchBrief,
    NarrativePack,
    SignalSummary,
)


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

def _candidate(
    asset: str = "BTC",
    direction: DirectionHint = DirectionHint.BULLISH,
    confidence: float = 0.75,
    urgency: SignalUrgency = SignalUrgency.SHORT_TERM,
    narrative: NarrativeLabel = NarrativeLabel.INSTITUTIONAL_ADOPTION,
    title: str = "Bitcoin ETF approved",
    source_id: str = "coindesk",
    impact: float = 0.80,
) -> SignalCandidate:
    c = SignalCandidate(
        document_id=f"doc-{asset}-{confidence}",
        source_id=source_id,
        asset=asset,
        direction_hint=direction,
        confidence=confidence,
        urgency=urgency,
        narrative_label=narrative,
        title=title,
        impact_score=impact,
        source_quality=0.85,
        supporting_evidence=["Institutional inflows growing"],
        contradicting_evidence=["Profit-taking expected"],
        risk_notes=["Low novelty — may be recycled"],
        recommended_next_step="Monitor BTC for follow-through.",
    )
    return c


@pytest.fixture
def builder() -> ResearchPackBuilder:
    return ResearchPackBuilder()


@pytest.fixture
def btc_candidates() -> list[SignalCandidate]:
    return [
        _candidate("BTC", confidence=0.80),
        _candidate("BTC", confidence=0.65, direction=DirectionHint.NEUTRAL),
        _candidate("BTC", confidence=0.75, narrative=NarrativeLabel.REGULATORY_RISK),
    ]


@pytest.fixture
def multi_asset_candidates() -> list[SignalCandidate]:
    return [
        _candidate("BTC", confidence=0.80),
        _candidate("ETH", confidence=0.70, narrative=NarrativeLabel.REGULATORY_RISK),
        _candidate("ETH", confidence=0.65),
        _candidate("NVDA", confidence=0.60, narrative=NarrativeLabel.MACRO_SHIFT),
    ]


# ──────────────────────────────────────────────
# AssetResearchPack
# ──────────────────────────────────────────────

class TestAssetResearchPack:
    def test_builds_for_btc(self, builder: ResearchPackBuilder, btc_candidates: list[SignalCandidate]) -> None:
        pack = builder.for_asset("BTC", btc_candidates)
        assert pack.asset == "BTC"
        assert pack.total_documents == 3

    def test_overall_confidence_is_average(self, builder: ResearchPackBuilder, btc_candidates: list[SignalCandidate]) -> None:
        pack = builder.for_asset("BTC", btc_candidates)
        expected = sum(c.confidence for c in btc_candidates) / len(btc_candidates)
        assert abs(pack.overall_confidence - expected) < 0.001

    def test_direction_consensus_bullish(self, builder: ResearchPackBuilder) -> None:
        candidates = [_candidate("BTC", direction=DirectionHint.BULLISH) for _ in range(5)]
        pack = builder.for_asset("BTC", candidates)
        assert pack.direction_consensus == DirectionHint.BULLISH

    def test_direction_consensus_mixed_when_split(self, builder: ResearchPackBuilder) -> None:
        candidates = [
            _candidate("BTC", direction=DirectionHint.BULLISH),
            _candidate("BTC", direction=DirectionHint.BULLISH),
            _candidate("BTC", direction=DirectionHint.BEARISH),
            _candidate("BTC", direction=DirectionHint.BEARISH),
        ]
        pack = builder.for_asset("BTC", candidates)
        assert pack.direction_consensus in (DirectionHint.MIXED, DirectionHint.BULLISH, DirectionHint.BEARISH)

    def test_urgency_is_max_urgency(self, builder: ResearchPackBuilder) -> None:
        candidates = [
            _candidate("BTC", urgency=SignalUrgency.MONITOR),
            _candidate("BTC", urgency=SignalUrgency.IMMEDIATE),
        ]
        pack = builder.for_asset("BTC", candidates)
        assert pack.urgency == SignalUrgency.IMMEDIATE

    def test_empty_candidates_returns_empty_pack(self, builder: ResearchPackBuilder) -> None:
        pack = builder.for_asset("BTC", [])
        assert pack.total_documents == 0
        assert pack.overall_confidence == 0.0

    def test_filters_to_correct_asset(self, builder: ResearchPackBuilder, multi_asset_candidates: list[SignalCandidate]) -> None:
        pack = builder.for_asset("ETH", multi_asset_candidates)
        assert pack.total_documents == 2
        assert pack.asset == "ETH"

    def test_to_dict_structure(self, builder: ResearchPackBuilder, btc_candidates: list[SignalCandidate]) -> None:
        d = builder.for_asset("BTC", btc_candidates).to_dict()
        assert "asset" in d
        assert "direction_consensus" in d
        assert "overall_confidence" in d
        assert "signals" in d
        assert "key_risk_notes" in d

    def test_deduplicated_evidence(self, builder: ResearchPackBuilder) -> None:
        # Same evidence text in multiple candidates should not repeat
        candidates = [_candidate("BTC") for _ in range(3)]
        pack = builder.for_asset("BTC", candidates)
        assert len(pack.top_supporting_evidence) == len(set(pack.top_supporting_evidence))


# ──────────────────────────────────────────────
# NarrativePack
# ──────────────────────────────────────────────

class TestNarrativePack:
    def test_builds_for_regulatory(self, builder: ResearchPackBuilder) -> None:
        candidates = [
            _candidate("BTC", narrative=NarrativeLabel.REGULATORY_RISK),
            _candidate("ETH", narrative=NarrativeLabel.REGULATORY_RISK),
        ]
        pack = builder.for_narrative(NarrativeLabel.REGULATORY_RISK, candidates)
        assert pack.narrative_label == NarrativeLabel.REGULATORY_RISK

    def test_affected_assets_populated(self, builder: ResearchPackBuilder) -> None:
        candidates = [
            _candidate("BTC", narrative=NarrativeLabel.REGULATORY_RISK),
            _candidate("ETH", narrative=NarrativeLabel.REGULATORY_RISK),
        ]
        pack = builder.for_narrative(NarrativeLabel.REGULATORY_RISK, candidates)
        assert "BTC" in pack.affected_assets
        assert "ETH" in pack.affected_assets

    def test_filters_to_correct_narrative(self, builder: ResearchPackBuilder, multi_asset_candidates: list[SignalCandidate]) -> None:
        pack = builder.for_narrative(NarrativeLabel.REGULATORY_RISK, multi_asset_candidates)
        assert all(s.narrative_label == NarrativeLabel.REGULATORY_RISK.value for s in pack.signals)

    def test_empty_narrative_pack(self, builder: ResearchPackBuilder) -> None:
        pack = builder.for_narrative(NarrativeLabel.HACK_EXPLOIT, [])
        assert len(pack.signals) == 0
        assert pack.overall_confidence == 0.0


# ──────────────────────────────────────────────
# BreakingNewsPack
# ──────────────────────────────────────────────

class TestBreakingNewsPack:
    def test_builds_cluster(self, builder: ResearchPackBuilder, btc_candidates: list[SignalCandidate]) -> None:
        pack = builder.for_breaking_cluster(btc_candidates, "BTC Breaking Cluster")
        assert pack.cluster_title == "BTC Breaking Cluster"
        assert pack.document_count == 3

    def test_uses_first_title_as_default(self, builder: ResearchPackBuilder, btc_candidates: list[SignalCandidate]) -> None:
        pack = builder.for_breaking_cluster(btc_candidates)
        assert len(pack.cluster_title) > 0

    def test_max_impact_score(self, builder: ResearchPackBuilder) -> None:
        candidates = [
            _candidate("BTC", impact=0.60),
            _candidate("BTC", impact=0.95),
        ]
        pack = builder.for_breaking_cluster(candidates)
        assert abs(pack.max_impact_score - 0.95) < 0.001


# ──────────────────────────────────────────────
# DailyResearchBrief
# ──────────────────────────────────────────────

class TestDailyResearchBrief:
    def test_builds_brief(self, builder: ResearchPackBuilder, multi_asset_candidates: list[SignalCandidate]) -> None:
        brief = builder.daily_brief(multi_asset_candidates, date="2024-01-15")
        assert brief.date == "2024-01-15"
        assert brief.total_signals == len(multi_asset_candidates)

    def test_empty_brief(self, builder: ResearchPackBuilder) -> None:
        brief = builder.daily_brief([])
        assert brief.total_signals == 0
        assert brief.market_sentiment == "neutral"

    def test_bullish_sentiment(self, builder: ResearchPackBuilder) -> None:
        candidates = [_candidate("BTC", direction=DirectionHint.BULLISH) for _ in range(6)]
        brief = builder.daily_brief(candidates)
        assert brief.market_sentiment == "positive"

    def test_bearish_sentiment(self, builder: ResearchPackBuilder) -> None:
        candidates = [_candidate("BTC", direction=DirectionHint.BEARISH) for _ in range(6)]
        brief = builder.daily_brief(candidates)
        assert brief.market_sentiment == "negative"

    def test_top_assets_limited(self, builder: ResearchPackBuilder) -> None:
        assets = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "LINK"]
        candidates = [_candidate(a) for a in assets]
        brief = builder.daily_brief(candidates, max_assets=3)
        assert len(brief.top_assets) <= 3

    def test_watchlist_hits_populated(self, builder: ResearchPackBuilder, multi_asset_candidates: list[SignalCandidate]) -> None:
        brief = builder.daily_brief(multi_asset_candidates)
        assert len(brief.watchlist_hits) > 0

    def test_to_dict_complete(self, builder: ResearchPackBuilder, multi_asset_candidates: list[SignalCandidate]) -> None:
        d = builder.daily_brief(multi_asset_candidates).to_dict()
        assert "date" in d
        assert "total_signals" in d
        assert "top_assets" in d
        assert "active_narratives" in d
        assert "market_sentiment" in d

    def test_breaking_cluster_for_urgent(self, builder: ResearchPackBuilder) -> None:
        candidates = [
            _candidate("BTC", urgency=SignalUrgency.IMMEDIATE),
            _candidate("ETH", urgency=SignalUrgency.IMMEDIATE),
        ]
        brief = builder.daily_brief(candidates)
        assert len(brief.breaking_clusters) > 0
