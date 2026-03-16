"""
Tests for NarrativeClusterEngine, NarrativeCluster, and clustering helpers.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.analysis.narratives.cluster import (
    NarrativeCluster,
    NarrativeClusterEngine,
    ClusterConfig,
    _jaccard,
)
from app.core.enums import NarrativeLabel, DirectionHint, SignalUrgency
from app.trading.signals.candidate import SignalCandidate


# ─────────────────────────────────────────────
# Helper: make a minimal SignalCandidate
# ─────────────────────────────────────────────

def _make_candidate(
    asset: str,
    label: NarrativeLabel = NarrativeLabel.INSTITUTIONAL_ADOPTION,
    direction: DirectionHint = DirectionHint.BULLISH,
    urgency: SignalUrgency = SignalUrgency.SHORT_TERM,
    confidence: float = 0.75,
    impact: float = 0.8,
    entities: list[str] | None = None,
    source_id: str = "src_a",
    generated_at: datetime | None = None,
) -> SignalCandidate:
    return SignalCandidate(
        id=f"sc-{asset.lower()}-{label.value[:4]}",
        asset=asset,
        title=f"{asset} signal: {label.value}",
        narrative_label=label,
        direction_hint=direction,
        urgency=urgency,
        confidence=confidence,
        impact_score=impact,
        matched_entities=entities or [],
        source_id=source_id,
        generated_at=generated_at or datetime.utcnow(),
    )


# ─────────────────────────────────────────────
# Jaccard helper
# ─────────────────────────────────────────────

class TestJaccard:
    def test_identical_sets(self):
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self):
        result = _jaccard({"a", "b"}, {"b", "c"})
        assert abs(result - (1 / 3)) < 0.001

    def test_empty_sets(self):
        assert _jaccard(set(), set()) == 1.0

    def test_one_empty(self):
        assert _jaccard({"a"}, set()) == 0.0


# ─────────────────────────────────────────────
# NarrativeCluster
# ─────────────────────────────────────────────

class TestNarrativeCluster:
    def test_to_dict_has_required_keys(self):
        now = datetime.utcnow()
        cluster = NarrativeCluster(
            cluster_id="nc-0001",
            label=NarrativeLabel.INSTITUTIONAL_ADOPTION,
            title="BTC ETF",
            assets=["BTC"],
            doc_count=3,
            first_seen=now,
            last_seen=now,
            velocity=0.5,
            avg_confidence=0.8,
            max_impact=0.9,
        )
        d = cluster.to_dict()
        assert d["cluster_id"] == "nc-0001"
        assert d["label"] == NarrativeLabel.INSTITUTIONAL_ADOPTION.value
        assert d["assets"] == ["BTC"]
        assert d["doc_count"] == 3
        assert "velocity" in d
        assert "is_accelerating" in d
        assert "is_cross_source" in d

    def test_cross_source_default_false(self):
        cluster = NarrativeCluster(
            cluster_id="nc-0002",
            label=NarrativeLabel.MACRO_SHIFT,
        )
        assert cluster.is_cross_source is False


# ─────────────────────────────────────────────
# ClusterConfig
# ─────────────────────────────────────────────

class TestClusterConfig:
    def test_defaults(self):
        cfg = ClusterConfig()
        assert cfg.min_cluster_size == 2
        assert cfg.merge_threshold == 0.30
        assert cfg.acceleration_window_hours == 6
        assert cfg.max_clusters == 20


# ─────────────────────────────────────────────
# NarrativeClusterEngine
# ─────────────────────────────────────────────

class TestNarrativeClusterEngine:
    def test_empty_candidates(self):
        engine = NarrativeClusterEngine()
        result = engine.cluster([])
        assert result == []

    def test_single_candidate_urgent_included(self):
        """Single-item clusters are included if urgency is immediate/short_term."""
        engine = NarrativeClusterEngine()
        candidate = _make_candidate(
            "BTC", urgency=SignalUrgency.IMMEDIATE,
            label=NarrativeLabel.INSTITUTIONAL_ADOPTION,
        )
        result = engine.cluster([candidate])
        assert len(result) >= 1

    def test_single_candidate_low_urgency_excluded(self):
        """Single-item clusters with low urgency are filtered out."""
        engine = NarrativeClusterEngine()
        candidate = _make_candidate(
            "BTC", urgency=SignalUrgency.LONG_TERM,
            label=NarrativeLabel.INSTITUTIONAL_ADOPTION,
        )
        result = engine.cluster([candidate])
        assert len(result) == 0

    def test_groups_same_label_together(self):
        """Multiple candidates with same label and asset merge into one cluster."""
        engine = NarrativeClusterEngine()
        candidates = [
            _make_candidate("BTC", label=NarrativeLabel.INSTITUTIONAL_ADOPTION),
            _make_candidate("BTC", label=NarrativeLabel.INSTITUTIONAL_ADOPTION),
        ]
        result = engine.cluster(candidates)
        assert len(result) == 1
        assert result[0].doc_count == 2

    def test_different_labels_separate_clusters(self):
        """Different narrative labels produce separate clusters."""
        engine = NarrativeClusterEngine()
        candidates = [
            _make_candidate("BTC", label=NarrativeLabel.INSTITUTIONAL_ADOPTION),
            _make_candidate("BTC", label=NarrativeLabel.INSTITUTIONAL_ADOPTION),
            _make_candidate("ETH", label=NarrativeLabel.TECH_UPGRADE),
            _make_candidate("ETH", label=NarrativeLabel.TECH_UPGRADE),
        ]
        result = engine.cluster(candidates)
        labels = {c.label for c in result}
        assert NarrativeLabel.INSTITUTIONAL_ADOPTION in labels
        assert NarrativeLabel.TECH_UPGRADE in labels

    def test_cross_source_detection(self):
        """Candidates from different source_ids set is_cross_source=True."""
        engine = NarrativeClusterEngine()
        candidates = [
            _make_candidate("BTC", label=NarrativeLabel.MACRO_SHIFT, source_id="src_a"),
            _make_candidate("BTC", label=NarrativeLabel.MACRO_SHIFT, source_id="src_b"),
        ]
        result = engine.cluster(candidates)
        assert len(result) == 1
        assert result[0].is_cross_source is True

    def test_same_source_not_cross_source(self):
        engine = NarrativeClusterEngine()
        candidates = [
            _make_candidate("ETH", label=NarrativeLabel.TECH_UPGRADE, source_id="src_a"),
            _make_candidate("ETH", label=NarrativeLabel.TECH_UPGRADE, source_id="src_a"),
        ]
        result = engine.cluster(candidates)
        assert result[0].is_cross_source is False

    def test_velocity_calculation(self):
        """Candidates all within 24h should contribute to velocity."""
        engine = NarrativeClusterEngine()
        now = datetime.utcnow()
        candidates = [
            _make_candidate("BTC", label=NarrativeLabel.MARKET_CRASH,
                            generated_at=now - timedelta(hours=i))
            for i in range(4)
        ]
        result = engine.cluster(candidates, now=now)
        assert len(result) == 1
        assert result[0].velocity > 0.0

    def test_acceleration_detection(self):
        """More docs in last 6h than prior 6h marks cluster as accelerating."""
        engine = NarrativeClusterEngine()
        now = datetime.utcnow()

        # 3 recent (last 6h) + 1 older (6-12h ago)
        recent = [
            _make_candidate("XRP", label=NarrativeLabel.REGULATORY_RISK,
                            generated_at=now - timedelta(hours=i))
            for i in range(1, 4)
        ]
        older = [
            _make_candidate("XRP", label=NarrativeLabel.REGULATORY_RISK,
                            generated_at=now - timedelta(hours=8))
        ]
        result = engine.cluster(recent + older, now=now)
        assert len(result) == 1
        assert result[0].is_accelerating is True

    def test_no_acceleration_when_equal(self):
        """Equal counts in both 6h windows → not accelerating."""
        engine = NarrativeClusterEngine()
        now = datetime.utcnow()

        cands = [
            _make_candidate("ETH", label=NarrativeLabel.TECH_UPGRADE,
                            generated_at=now - timedelta(hours=1)),
            _make_candidate("ETH", label=NarrativeLabel.TECH_UPGRADE,
                            generated_at=now - timedelta(hours=8)),
        ]
        result = engine.cluster(cands, now=now)
        # Only 1 recent doc → is_accelerating requires >= 2
        for c in result:
            assert c.is_accelerating is False

    def test_sorted_by_doc_count_desc(self):
        """Returned clusters are sorted by doc_count descending."""
        engine = NarrativeClusterEngine()
        btc_cands = [
            _make_candidate("BTC", label=NarrativeLabel.INSTITUTIONAL_ADOPTION)
            for _ in range(4)
        ]
        eth_cands = [
            _make_candidate("ETH", label=NarrativeLabel.TECH_UPGRADE)
            for _ in range(2)
        ]
        result = engine.cluster(btc_cands + eth_cands)
        assert result[0].doc_count >= result[-1].doc_count

    def test_respects_max_clusters(self):
        """Engine respects max_clusters config."""
        cfg = ClusterConfig(min_cluster_size=1, max_clusters=2)
        engine = NarrativeClusterEngine(config=cfg)

        # Create candidates with 4 distinct labels
        all_labels = [
            NarrativeLabel.INSTITUTIONAL_ADOPTION,
            NarrativeLabel.TECH_UPGRADE,
            NarrativeLabel.REGULATORY_RISK,
            NarrativeLabel.MACRO_SHIFT,
        ]
        candidates = []
        for label in all_labels:
            candidates.append(
                _make_candidate("BTC", label=label, urgency=SignalUrgency.IMMEDIATE)
            )
        result = engine.cluster(candidates)
        assert len(result) <= 2

    def test_assets_aggregated(self):
        """Assets from multiple candidates are aggregated in the cluster."""
        engine = NarrativeClusterEngine()
        candidates = [
            _make_candidate("BTC", label=NarrativeLabel.INSTITUTIONAL_ADOPTION),
            _make_candidate("ETH", label=NarrativeLabel.INSTITUTIONAL_ADOPTION,
                            entities=["BlackRock"]),
        ]
        result = engine.cluster(candidates)
        # BTC and ETH have 50% Jaccard on asset set  → may or may not merge
        # But both should appear somewhere in the results
        all_assets = [a for c in result for a in c.assets]
        assert "BTC" in all_assets
        assert "ETH" in all_assets


# ─────────────────────────────────────────────
# merge_clusters
# ─────────────────────────────────────────────

class TestMergeClusters:
    def test_merge_high_overlap_different_labels(self):
        """Clusters with ≥60% asset overlap and different labels get merged."""
        engine = NarrativeClusterEngine()
        c1 = NarrativeCluster(
            cluster_id="nc-0001",
            label=NarrativeLabel.INSTITUTIONAL_ADOPTION,
            assets=["BTC", "ETH"],
            doc_count=3,
        )
        c2 = NarrativeCluster(
            cluster_id="nc-0002",
            label=NarrativeLabel.MACRO_SHIFT,
            assets=["BTC", "ETH"],
            doc_count=2,
        )
        result = engine.merge_clusters([c1, c2])
        assert len(result) == 1
        merged = result[0]
        assert merged.is_cross_source is True
        assert merged.doc_count == 5

    def test_no_merge_same_label(self):
        """Same-label clusters are not merged even with identical assets."""
        engine = NarrativeClusterEngine()
        c1 = NarrativeCluster(
            cluster_id="nc-0001",
            label=NarrativeLabel.MACRO_SHIFT,
            assets=["BTC"],
            doc_count=2,
        )
        c2 = NarrativeCluster(
            cluster_id="nc-0002",
            label=NarrativeLabel.MACRO_SHIFT,
            assets=["BTC"],
            doc_count=2,
        )
        result = engine.merge_clusters([c1, c2])
        assert len(result) == 2

    def test_no_merge_low_overlap(self):
        """Clusters with <60% asset overlap are not merged."""
        engine = NarrativeClusterEngine()
        c1 = NarrativeCluster(
            cluster_id="nc-0001",
            label=NarrativeLabel.INSTITUTIONAL_ADOPTION,
            assets=["BTC"],
            doc_count=2,
        )
        c2 = NarrativeCluster(
            cluster_id="nc-0002",
            label=NarrativeLabel.MACRO_SHIFT,
            assets=["ETH"],
            doc_count=2,
        )
        result = engine.merge_clusters([c1, c2])
        assert len(result) == 2

    def test_single_cluster_no_merge(self):
        engine = NarrativeClusterEngine()
        c1 = NarrativeCluster(
            cluster_id="nc-0001",
            label=NarrativeLabel.TECH_UPGRADE,
            assets=["ETH"],
        )
        result = engine.merge_clusters([c1])
        assert len(result) == 1
