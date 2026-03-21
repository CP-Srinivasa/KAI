"""Tests for the Narrative Clustering Engine (Sprint 28).

Invariants verified:
- I-177: Pure computation — no DB, no LLM, no network.
- I-178: All cluster fields derived from SignalCandidate inputs.
- I-179: NarrativeLabel.UNKNOWN is valid.
- I-180: execution_enabled always False.
- I-181: Deterministic output for same input.
- I-182: dominant_direction in {bullish, bearish, neutral, mixed}.
- I-183: is_accelerating is advisory only (not a gate).
- I-184: Persistence (save_narrative_clusters) is JSONL only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.analysis.narratives.cluster import (
    ClusterConfig,
    NarrativeCluster,
    NarrativeClusterEngine,
    _jaccard,
    save_narrative_clusters,
)
from app.core.enums import MarketScope, NarrativeLabel, SentimentLabel
from app.research.signals import SignalCandidate

# ── Helpers ───────────────────────────────────────────────────────────────────


_NOW = datetime(2026, 3, 20, 12, 0, 0, tzinfo=UTC)


def _make_candidate(
    signal_id: str = "sig_1",
    target_asset: str = "BTC",
    direction_hint: str = "bullish",
    priority: int = 8,
    confidence: float = 0.8,
    affected_assets: list[str] | None = None,
    analysis_source: str = "external_llm",
    published_at: datetime | None = None,
    sentiment: SentimentLabel = SentimentLabel.BULLISH,
) -> SignalCandidate:
    return SignalCandidate(
        signal_id=signal_id,
        document_id=f"doc_{signal_id}",
        target_asset=target_asset,
        direction_hint=direction_hint,
        confidence=confidence,
        supporting_evidence=f"Evidence for {signal_id}",
        contradicting_evidence="None",
        risk_notes="Low",
        source_quality=0.9,
        recommended_next_step="Review",
        analysis_source=analysis_source,
        priority=priority,
        sentiment=sentiment,
        affected_assets=affected_assets or [target_asset],
        market_scope=MarketScope.CRYPTO,
        published_at=published_at or _NOW - timedelta(hours=2),
    )


# ── Jaccard helper ────────────────────────────────────────────────────────────


def test_jaccard_identical():
    a = frozenset({"BTC", "ETH"})
    assert _jaccard(a, a) == 1.0


def test_jaccard_disjoint():
    a = frozenset({"BTC"})
    b = frozenset({"ETH"})
    assert _jaccard(a, b) == 0.0


def test_jaccard_partial():
    a = frozenset({"BTC", "ETH"})
    b = frozenset({"BTC", "SOL"})
    # intersection=1, union=3
    assert abs(_jaccard(a, b) - 1 / 3) < 1e-9


def test_jaccard_both_empty():
    assert _jaccard(frozenset(), frozenset()) == 1.0


# ── ClusterConfig ─────────────────────────────────────────────────────────────


def test_cluster_config_defaults():
    cfg = ClusterConfig()
    assert cfg.min_cluster_size == 2
    assert cfg.merge_threshold == 0.30
    assert cfg.acceleration_window_hours == 6
    assert cfg.max_clusters == 20


def test_cluster_config_custom():
    cfg = ClusterConfig(min_cluster_size=1, merge_threshold=0.5, max_clusters=5)
    assert cfg.min_cluster_size == 1
    assert cfg.max_clusters == 5


# ── NarrativeCluster ──────────────────────────────────────────────────────────


def test_narrative_cluster_execution_enabled_always_false():
    """I-180: execution_enabled must always be False."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    c1 = _make_candidate("s1", "BTC")
    c2 = _make_candidate("s2", "BTC")
    clusters = engine.cluster([c1, c2], now=_NOW)
    assert all(not cl.execution_enabled for cl in clusters)


def test_narrative_cluster_to_json_dict_structure():
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    cand = _make_candidate("s1", "BTC")
    clusters = engine.cluster([cand], now=_NOW)
    assert clusters, "Expected at least one cluster"
    d = clusters[0].to_json_dict()

    required_keys = {
        "cluster_id", "label", "title", "assets", "entities",
        "sources", "candidate_ids", "doc_count", "first_seen",
        "last_seen", "velocity", "dominant_direction",
        "avg_confidence", "max_impact", "is_accelerating",
        "is_cross_source", "execution_enabled",
    }
    assert required_keys <= set(d.keys())
    assert d["execution_enabled"] is False  # I-180


# ── NarrativeClusterEngine.cluster() ─────────────────────────────────────────


def test_engine_empty_input_returns_empty():
    engine = NarrativeClusterEngine()
    assert engine.cluster([]) == []


def test_engine_clusters_same_assets_together():
    """Candidates sharing BTC should land in the same sub-cluster."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    c1 = _make_candidate("s1", "BTC", affected_assets=["BTC", "ETH"])
    c2 = _make_candidate("s2", "BTC", affected_assets=["BTC"])
    clusters = engine.cluster([c1, c2], now=_NOW)
    # BTC overlap → should be in one cluster
    assert len(clusters) == 1
    assert clusters[0].doc_count == 2


def test_engine_separates_disjoint_assets():
    """Candidates with no asset overlap → different clusters."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    c1 = _make_candidate("s1", "BTC", affected_assets=["BTC"])
    c2 = _make_candidate("s2", "ETH", affected_assets=["ETH"])
    clusters = engine.cluster([c1, c2], now=_NOW)
    assert len(clusters) == 2


def test_engine_min_cluster_size_filters_small_clusters():
    """With min_cluster_size=2 and no urgent signals, single-doc clusters are dropped."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=2))
    c1 = _make_candidate("s1", "BTC", affected_assets=["BTC"], priority=5)
    c2 = _make_candidate("s2", "ETH", affected_assets=["ETH"], priority=5)
    # Each in its own sub-cluster of size 1, neither is urgent (priority<8)
    clusters = engine.cluster([c1, c2], now=_NOW)
    assert clusters == []


def test_engine_keeps_urgent_single_doc_cluster():
    """Even with min_cluster_size=2, a priority-8 singleton is kept."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=2))
    c1 = _make_candidate("s1", "BTC", affected_assets=["BTC"], priority=8)
    clusters = engine.cluster([c1], now=_NOW)
    assert len(clusters) == 1


def test_engine_label_map_groups_by_label():
    """Candidates with different labels → separate clusters even if assets overlap."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    c1 = _make_candidate("s1", "BTC", affected_assets=["BTC"])
    c2 = _make_candidate("s2", "BTC", affected_assets=["BTC"])
    label_map = {
        "s1": NarrativeLabel.REGULATORY_RISK,
        "s2": NarrativeLabel.INSTITUTIONAL_ADOPTION,
    }
    clusters = engine.cluster([c1, c2], label_map=label_map, now=_NOW)
    assert len(clusters) == 2
    labels = {cl.label for cl in clusters}
    assert NarrativeLabel.REGULATORY_RISK in labels
    assert NarrativeLabel.INSTITUTIONAL_ADOPTION in labels


def test_engine_unknown_label_default():
    """Candidates without label_map entry receive NarrativeLabel.UNKNOWN (I-179)."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    c1 = _make_candidate("s1", "BTC")
    clusters = engine.cluster([c1], now=_NOW)
    assert clusters[0].label == NarrativeLabel.UNKNOWN


def test_engine_dominant_direction_bullish():
    """Majority bullish → dominant_direction='bullish' (I-182)."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    c1 = _make_candidate("s1", direction_hint="bullish", affected_assets=["BTC"])
    c2 = _make_candidate("s2", direction_hint="bullish", affected_assets=["BTC"])
    c3 = _make_candidate("s3", direction_hint="bearish", affected_assets=["BTC"])
    clusters = engine.cluster([c1, c2, c3], now=_NOW)
    assert clusters[0].dominant_direction == "bullish"


def test_engine_dominant_direction_mixed_on_tie():
    """Equal bullish and bearish → dominant_direction='mixed' (I-182)."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    c1 = _make_candidate("s1", direction_hint="bullish", affected_assets=["BTC"])
    c2 = _make_candidate("s2", direction_hint="bearish", affected_assets=["BTC"])
    clusters = engine.cluster([c1, c2], now=_NOW)
    assert clusters[0].dominant_direction == "mixed"


def test_engine_sorted_by_doc_count_descending():
    """Larger clusters appear first."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    btc_cands = [_make_candidate(f"s{i}", "BTC", affected_assets=["BTC"]) for i in range(3)]
    eth_cand = _make_candidate("s4", "ETH", affected_assets=["ETH"])
    clusters = engine.cluster(btc_cands + [eth_cand], now=_NOW)
    assert clusters[0].doc_count >= clusters[-1].doc_count


def test_engine_max_clusters_cap():
    """No more than max_clusters returned."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1, max_clusters=2))
    candidates = [
        _make_candidate(f"s{i}", f"ASSET{i}", affected_assets=[f"ASSET{i}"])
        for i in range(10)
    ]
    clusters = engine.cluster(candidates, now=_NOW)
    assert len(clusters) <= 2


def test_engine_deterministic_output():
    """Same input → same output regardless of call order (I-181)."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    cands = [
        _make_candidate("s1", "BTC", affected_assets=["BTC"]),
        _make_candidate("s2", "ETH", affected_assets=["ETH"]),
        _make_candidate("s3", "BTC", affected_assets=["BTC"]),
    ]
    r1 = engine.cluster(cands, now=_NOW)
    r2 = engine.cluster(cands, now=_NOW)
    assert [c.cluster_id for c in r1] == [c.cluster_id for c in r2]
    assert [c.doc_count for c in r1] == [c.doc_count for c in r2]


# ── Velocity & acceleration ───────────────────────────────────────────────────


def test_engine_velocity_counts_last_24h():
    """Docs published in last 24h contribute to velocity."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    recent = _make_candidate("s1", "BTC", published_at=_NOW - timedelta(hours=1))
    old = _make_candidate("s2", "BTC", published_at=_NOW - timedelta(hours=25))
    clusters = engine.cluster([recent, old], now=_NOW)
    # Only 1 in last 24h → velocity = 1/24
    assert abs(clusters[0].velocity - 1 / 24) < 0.01


def test_engine_is_accelerating_true():
    """is_accelerating=True when last window has more docs than prior (I-183)."""
    engine = NarrativeClusterEngine(
        ClusterConfig(min_cluster_size=1, acceleration_window_hours=6)
    )
    # 2 docs in last 6h, 0 in prior 6h → accelerating
    c1 = _make_candidate(
        "s1",
        "BTC",
        affected_assets=["BTC"],
        published_at=_NOW - timedelta(hours=1),
    )
    c2 = _make_candidate(
        "s2",
        "BTC",
        affected_assets=["BTC"],
        published_at=_NOW - timedelta(hours=2),
    )
    clusters = engine.cluster([c1, c2], now=_NOW)
    assert clusters[0].is_accelerating is True


def test_engine_is_accelerating_false_when_old():
    """is_accelerating=False when no recent docs."""
    engine = NarrativeClusterEngine(
        ClusterConfig(min_cluster_size=1, acceleration_window_hours=6)
    )
    c1 = _make_candidate(
        "s1",
        "BTC",
        affected_assets=["BTC"],
        published_at=_NOW - timedelta(hours=20),
    )
    c2 = _make_candidate(
        "s2",
        "BTC",
        affected_assets=["BTC"],
        published_at=_NOW - timedelta(hours=22),
    )
    clusters = engine.cluster([c1, c2], now=_NOW)
    assert clusters[0].is_accelerating is False


# ── merge_clusters ────────────────────────────────────────────────────────────


def test_merge_clusters_empty():
    engine = NarrativeClusterEngine()
    assert engine.merge_clusters([]) == []


def test_merge_clusters_high_overlap():
    """Clusters with ≥60% asset overlap get merged."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    # Build two separate clusters manually for merge test
    # Actually c1+c2 already cluster together; test merge directly with pre-built clusters
    cluster_a = NarrativeCluster(
        cluster_id="nc-0001",
        label=NarrativeLabel.UNKNOWN,
        title="A",
        assets=["BTC", "ETH"],
        entities=[],
        sources=["external_llm"],
        candidate_ids=["s1"],
        doc_count=1,
        first_seen=_NOW,
        last_seen=_NOW,
        velocity=0.0,
        dominant_direction="bullish",
        avg_confidence=0.8,
        max_impact=0.8,
        is_accelerating=False,
        is_cross_source=False,
    )
    cluster_b = NarrativeCluster(
        cluster_id="nc-0002",
        label=NarrativeLabel.UNKNOWN,
        title="B",
        assets=["BTC", "ETH"],
        entities=[],
        sources=["external_llm"],
        candidate_ids=["s2"],
        doc_count=1,
        first_seen=_NOW,
        last_seen=_NOW,
        velocity=0.0,
        dominant_direction="bullish",
        avg_confidence=0.7,
        max_impact=0.7,
        is_accelerating=False,
        is_cross_source=False,
    )
    merged = engine.merge_clusters([cluster_a, cluster_b])
    assert len(merged) == 1
    assert merged[0].doc_count == 2


def test_merge_clusters_low_overlap_no_merge():
    """Clusters with low asset overlap remain separate."""
    engine = NarrativeClusterEngine()
    cluster_a = NarrativeCluster(
        cluster_id="nc-0001",
        label=NarrativeLabel.UNKNOWN,
        title="A",
        assets=["BTC"],
        entities=[],
        sources=["external_llm"],
        candidate_ids=["s1"],
        doc_count=2,
        first_seen=_NOW,
        last_seen=_NOW,
        velocity=0.0,
        dominant_direction="bullish",
        avg_confidence=0.8,
        max_impact=0.8,
        is_accelerating=False,
        is_cross_source=False,
    )
    cluster_b = NarrativeCluster(
        cluster_id="nc-0002",
        label=NarrativeLabel.UNKNOWN,
        title="B",
        assets=["ETH"],
        entities=[],
        sources=["external_llm"],
        candidate_ids=["s2"],
        doc_count=2,
        first_seen=_NOW,
        last_seen=_NOW,
        velocity=0.0,
        dominant_direction="bearish",
        avg_confidence=0.6,
        max_impact=0.6,
        is_accelerating=False,
        is_cross_source=False,
    )
    merged = engine.merge_clusters([cluster_a, cluster_b])
    assert len(merged) == 2


# ── save_narrative_clusters ───────────────────────────────────────────────────


def test_save_narrative_clusters_creates_valid_jsonl(tmp_path: Path):
    """I-184: persistence is JSONL only."""
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    c1 = _make_candidate("s1", "BTC", affected_assets=["BTC"])
    c2 = _make_candidate("s2", "BTC", affected_assets=["BTC"])
    clusters = engine.cluster([c1, c2], now=_NOW)

    out = tmp_path / "clusters.jsonl"
    path = save_narrative_clusters(clusters, out)

    assert path.exists()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == len(clusters)
    for line in lines:
        data = json.loads(line)
        assert "cluster_id" in data
        assert data["execution_enabled"] is False  # I-180


def test_save_narrative_clusters_creates_parent_dirs(tmp_path: Path):
    engine = NarrativeClusterEngine(ClusterConfig(min_cluster_size=1))
    c1 = _make_candidate("s1", "BTC", affected_assets=["BTC"])
    clusters = engine.cluster([c1], now=_NOW)
    out = tmp_path / "subdir" / "nested" / "clusters.jsonl"
    path = save_narrative_clusters(clusters, out)
    assert path.exists()
