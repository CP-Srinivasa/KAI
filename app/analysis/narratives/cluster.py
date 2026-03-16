"""
Narrative Clustering
=====================
Groups documents/signals into narrative clusters based on shared
entities, assets, topics, and tags.

No ML required — purely structural/rule-based clustering.

Features:
  1. Cluster by topic/entity/asset overlap (Jaccard similarity)
  2. Narrative label assignment (reuses NarrativeLabel enum)
  3. Narrative acceleration detection (velocity = docs per hour)
  4. Cross-source narrative merge

A NarrativeCluster is a group of related documents sharing a common
narrative theme. It is input for Research Packs and Alert Digest.

Usage:
    engine = NarrativeClusterEngine()
    clusters = engine.cluster(signal_candidates)
    for cluster in clusters:
        print(cluster.label, cluster.velocity, cluster.assets)
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.core.enums import DirectionHint, NarrativeLabel
from app.core.logging import get_logger
from app.trading.signals.candidate import SignalCandidate

logger = get_logger(__name__)


@dataclass
class NarrativeCluster:
    """
    A group of signal candidates sharing a narrative theme.
    """
    cluster_id: str
    label: NarrativeLabel
    title: str = ""
    assets: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    doc_count: int = 0

    # Temporal metrics
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    velocity: float = 0.0       # documents per hour in last 24h

    # Signal aggregates
    dominant_direction: DirectionHint = DirectionHint.NEUTRAL
    avg_confidence: float = 0.0
    max_impact: float = 0.0
    is_accelerating: bool = False   # velocity increasing in last 6h vs prior 6h

    # Cross-source flag
    is_cross_source: bool = False   # appears in multiple distinct source_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label.value,
            "title": self.title,
            "assets": self.assets,
            "entities": self.entities,
            "sources": self.sources,
            "doc_count": self.doc_count,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "velocity": round(self.velocity, 3),
            "dominant_direction": self.dominant_direction.value,
            "avg_confidence": round(self.avg_confidence, 3),
            "max_impact": round(self.max_impact, 3),
            "is_accelerating": self.is_accelerating,
            "is_cross_source": self.is_cross_source,
        }


@dataclass
class ClusterConfig:
    min_cluster_size: int = 2           # Minimum docs to form a cluster
    merge_threshold: float = 0.30       # Jaccard threshold to merge clusters
    acceleration_window_hours: int = 6  # Window for velocity comparison
    max_clusters: int = 20


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union > 0 else 0.0


def _assign_label_from_candidate(candidate: SignalCandidate) -> NarrativeLabel:
    return candidate.narrative_label


class NarrativeClusterEngine:
    """
    Groups SignalCandidates into NarrativeClusters.

    Algorithm:
    1. Start with each candidate in its own proto-cluster (by label)
    2. Within each label group, merge by asset/entity overlap
    3. Compute velocity and acceleration metrics
    4. Return sorted clusters (by doc_count desc)
    """

    def __init__(self, config: ClusterConfig | None = None) -> None:
        self._config = config or ClusterConfig()

    def cluster(
        self,
        candidates: list[SignalCandidate],
        now: datetime | None = None,
    ) -> list[NarrativeCluster]:
        """
        Cluster candidates into NarrativeClusters.

        Args:
            candidates: List of SignalCandidates from the analysis pipeline.
            now:        Reference time for velocity calculation (default: utcnow).

        Returns:
            List of NarrativeCluster, sorted by doc_count descending.
        """
        if not candidates:
            return []

        now = now or datetime.utcnow()

        # Group by narrative label first
        by_label: dict[NarrativeLabel, list[SignalCandidate]] = {}
        for c in candidates:
            label = _assign_label_from_candidate(c)
            by_label.setdefault(label, []).append(c)

        clusters: list[NarrativeCluster] = []
        cluster_counter = 0

        for label, group in by_label.items():
            # Within label group, sub-cluster by asset overlap
            sub_clusters: list[list[SignalCandidate]] = []
            for candidate in group:
                merged = False
                c_assets = {candidate.asset}
                c_entities = set(candidate.matched_entities)
                for sub in sub_clusters:
                    existing_assets = {c.asset for c in sub}
                    existing_entities = {e for c in sub for e in c.matched_entities}
                    sim = _jaccard(c_assets, existing_assets)
                    entity_sim = _jaccard(c_entities, existing_entities)
                    if sim >= self._config.merge_threshold or entity_sim >= self._config.merge_threshold:
                        sub.append(candidate)
                        merged = True
                        break
                if not merged:
                    sub_clusters.append([candidate])

            for sub in sub_clusters:
                if len(sub) < self._config.min_cluster_size:
                    # Still include single-item clusters for important narratives
                    if not any(c.urgency.value in ("immediate", "short_term") for c in sub):
                        continue

                cluster_counter += 1
                cluster = self._build_cluster(
                    cluster_id=f"nc-{cluster_counter:04d}",
                    label=label,
                    candidates=sub,
                    now=now,
                )
                clusters.append(cluster)

        # Sort by doc_count desc, then avg_confidence
        clusters.sort(key=lambda c: (c.doc_count, c.avg_confidence), reverse=True)
        result = clusters[: self._config.max_clusters]

        logger.info(
            "narrative_clusters_built",
            total=len(result),
            labels=list({c.label.value for c in result}),
        )
        return result

    def _build_cluster(
        self,
        cluster_id: str,
        label: NarrativeLabel,
        candidates: list[SignalCandidate],
        now: datetime,
    ) -> NarrativeCluster:
        # Asset/entity/source aggregation
        assets = list(dict.fromkeys(c.asset for c in candidates))
        entities = list(dict.fromkeys(e for c in candidates for e in c.matched_entities))
        sources = list(dict.fromkeys(c.source_id for c in candidates if c.source_id))

        # Timestamps
        times = [c.generated_at for c in candidates if c.generated_at]
        first_seen = min(times) if times else None
        last_seen = max(times) if times else None

        # Velocity: docs per hour in last 24h
        window_start = now - timedelta(hours=24)
        recent = [c for c in candidates if c.generated_at and c.generated_at >= window_start]
        velocity = len(recent) / 24.0 if recent else 0.0

        # Acceleration: compare last 6h vs prior 6h
        t_now = now
        t_6h = now - timedelta(hours=6)
        t_12h = now - timedelta(hours=12)
        last_6h_count = sum(1 for c in candidates if c.generated_at and t_6h <= c.generated_at <= t_now)
        prev_6h_count = sum(1 for c in candidates if c.generated_at and t_12h <= c.generated_at < t_6h)
        is_accelerating = last_6h_count > prev_6h_count and last_6h_count >= 2

        # Direction consensus
        dir_counts: Counter[str] = Counter(c.direction_hint.value for c in candidates)
        top_dir = dir_counts.most_common(1)
        dominant_direction = DirectionHint(top_dir[0][0]) if top_dir else DirectionHint.NEUTRAL

        avg_confidence = sum(c.confidence for c in candidates) / len(candidates)
        max_impact = max(c.impact_score for c in candidates)

        # Title: use most common/impactful source title
        sorted_by_impact = sorted(candidates, key=lambda c: c.impact_score, reverse=True)
        title = sorted_by_impact[0].title if sorted_by_impact else ""

        return NarrativeCluster(
            cluster_id=cluster_id,
            label=label,
            title=title,
            assets=assets[:10],
            entities=entities[:10],
            sources=sources[:10],
            candidate_ids=[c.id for c in candidates],
            doc_count=len(candidates),
            first_seen=first_seen,
            last_seen=last_seen,
            velocity=round(velocity, 4),
            dominant_direction=dominant_direction,
            avg_confidence=round(avg_confidence, 3),
            max_impact=round(max_impact, 3),
            is_accelerating=is_accelerating,
            is_cross_source=len(sources) > 1,
        )

    def merge_clusters(
        self,
        clusters: list[NarrativeCluster],
    ) -> list[NarrativeCluster]:
        """
        Merge clusters with high asset overlap across different labels.
        Used for cross-source narrative deduplication.
        """
        if len(clusters) <= 1:
            return clusters

        merged: list[NarrativeCluster] = []
        used: set[str] = set()

        for i, c1 in enumerate(clusters):
            if c1.cluster_id in used:
                continue
            group = [c1]
            for j, c2 in enumerate(clusters):
                if i == j or c2.cluster_id in used:
                    continue
                sim = _jaccard(set(c1.assets), set(c2.assets))
                if sim >= 0.60 and c1.label != c2.label:
                    group.append(c2)
                    used.add(c2.cluster_id)

            if len(group) == 1:
                merged.append(c1)
            else:
                # Merge into primary (highest doc_count)
                primary = max(group, key=lambda c: c.doc_count)
                for other in group:
                    if other.cluster_id == primary.cluster_id:
                        continue
                    primary.assets = list(dict.fromkeys(primary.assets + other.assets))[:10]
                    primary.entities = list(dict.fromkeys(primary.entities + other.entities))[:10]
                    primary.sources = list(dict.fromkeys(primary.sources + other.sources))[:10]
                    primary.candidate_ids += other.candidate_ids
                    primary.doc_count += other.doc_count
                    primary.is_cross_source = True
                merged.append(primary)
            used.add(c1.cluster_id)

        return merged
