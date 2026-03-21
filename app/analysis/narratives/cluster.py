"""Narrative Clustering Engine.

Groups SignalCandidates into thematic narrative clusters based on shared
NarrativeLabel and Jaccard similarity on affected_assets.

Pure deterministic computation — no DB, no LLM, no network.

Invariants (I-177–I-184):
- I-177: NarrativeClusterEngine is pure computation — no DB, no LLM, no network.
- I-178: All cluster fields are computed exclusively from SignalCandidate inputs.
- I-179: NarrativeLabel.UNKNOWN is valid and never dropped from clustering.
- I-180: NarrativeCluster.execution_enabled MUST always be False.
- I-181: Clusters are stable and reproducible for the same input (deterministic).
- I-182: dominant_direction MUST be one of bullish/bearish/neutral/mixed.
- I-183: is_accelerating is an advisory heuristic — never a trading signal.
- I-184: MCP/CLI narrative cluster surfaces are read-only projections.
"""

from __future__ import annotations

import itertools
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.enums import NarrativeLabel
from app.research.signals import SignalCandidate

_CLUSTER_ID_PREFIX = "nc"
_VALID_DIRECTIONS = frozenset({"bullish", "bearish", "neutral", "mixed"})


@dataclass(frozen=True)
class ClusterConfig:
    """Configuration for NarrativeClusterEngine."""

    min_cluster_size: int = 2
    merge_threshold: float = 0.30
    acceleration_window_hours: int = 6
    max_clusters: int = 20


@dataclass(frozen=True)
class NarrativeCluster:
    """A group of SignalCandidates sharing a narrative theme.

    execution_enabled is always False (I-180) — this is a research artifact only.
    """

    cluster_id: str
    label: NarrativeLabel
    title: str
    assets: list[str]
    entities: list[str]
    sources: list[str]
    candidate_ids: list[str]
    doc_count: int
    first_seen: datetime | None
    last_seen: datetime | None
    velocity: float
    dominant_direction: str
    avg_confidence: float
    max_impact: float
    is_accelerating: bool
    is_cross_source: bool
    execution_enabled: bool = False  # I-180: always False

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label.value,
            "title": self.title,
            "assets": self.assets,
            "entities": self.entities,
            "sources": self.sources,
            "candidate_ids": self.candidate_ids,
            "doc_count": self.doc_count,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "velocity": self.velocity,
            "dominant_direction": self.dominant_direction,
            "avg_confidence": self.avg_confidence,
            "max_impact": self.max_impact,
            "is_accelerating": self.is_accelerating,
            "is_cross_source": self.is_cross_source,
            "execution_enabled": self.execution_enabled,
        }


# ── Internal helpers ──────────────────────────────────────────────────────────


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two frozensets."""
    union = a | b
    if not union:
        return 1.0  # both empty → identical
    return len(a & b) / len(union)


def _compute_dominant_direction(candidates: list[SignalCandidate]) -> str:
    """Most common direction_hint; 'mixed' on tie. Always in _VALID_DIRECTIONS."""
    counter: Counter[str] = Counter(c.direction_hint for c in candidates)
    if not counter:
        return "neutral"
    most_common = counter.most_common(2)
    if len(most_common) == 2 and most_common[0][1] == most_common[1][1]:
        return "mixed"
    direction = most_common[0][0]
    return direction if direction in _VALID_DIRECTIONS else "neutral"


def _dominant_from_weighted(
    directions: list[str],
    weights: list[int],
) -> str:
    """Weighted dominant direction — used when merging existing clusters."""
    counter: Counter[str] = Counter()
    for d, w in zip(directions, weights, strict=True):
        counter[d] += w
    if not counter:
        return "neutral"
    most_common = counter.most_common(2)
    if len(most_common) == 2 and most_common[0][1] == most_common[1][1]:
        return "mixed"
    direction = most_common[0][0]
    return direction if direction in _VALID_DIRECTIONS else "neutral"


def _compute_velocity(candidates: list[SignalCandidate], now: datetime) -> float:
    """Docs per hour published in last 24 hours."""
    cutoff = now - timedelta(hours=24)
    recent = sum(
        1 for c in candidates if c.published_at is not None and c.published_at >= cutoff
    )
    return round(recent / 24.0, 4)


def _compute_is_accelerating(
    candidates: list[SignalCandidate],
    now: datetime,
    window_hours: int,
) -> bool:
    """True if last N hours > prev N hours AND last N hours >= 2 (I-183: advisory)."""
    last_cutoff = now - timedelta(hours=window_hours)
    prev_cutoff = now - timedelta(hours=window_hours * 2)
    last_count = sum(
        1 for c in candidates
        if c.published_at is not None and c.published_at >= last_cutoff
    )
    prev_count = sum(
        1 for c in candidates
        if c.published_at is not None and prev_cutoff <= c.published_at < last_cutoff
    )
    return last_count > prev_count and last_count >= 2


def _build_cluster(
    cluster_id: str,
    label: NarrativeLabel,
    candidates: list[SignalCandidate],
    *,
    now: datetime,
    acceleration_window_hours: int,
) -> NarrativeCluster:
    """Build a NarrativeCluster from a group of candidates."""
    assets = sorted({a for c in candidates for a in c.affected_assets})[:10]
    sources = sorted({c.analysis_source for c in candidates})[:10]
    candidate_ids = [c.signal_id for c in candidates]

    timestamps = [c.published_at for c in candidates if c.published_at is not None]
    first_seen = min(timestamps) if timestamps else None
    last_seen = max(timestamps) if timestamps else None

    # Title: supporting_evidence from highest-priority candidate, truncated
    best = max(candidates, key=lambda c: (c.priority, c.confidence))
    raw_title = best.supporting_evidence or best.target_asset or "Signal cluster"
    title = raw_title[:80] if len(raw_title) > 80 else raw_title

    dominant_direction = _compute_dominant_direction(candidates)
    velocity = _compute_velocity(candidates, now)
    is_accelerating = _compute_is_accelerating(candidates, now, acceleration_window_hours)
    avg_confidence = round(sum(c.confidence for c in candidates) / len(candidates), 4)
    max_impact = round(max(c.priority for c in candidates) / 10.0, 4)
    is_cross_source = len({c.analysis_source for c in candidates}) > 1

    return NarrativeCluster(
        cluster_id=cluster_id,
        label=label,
        title=title,
        assets=assets,
        entities=[],  # Sprint 28: entities not yet in SignalCandidate
        sources=sources,
        candidate_ids=candidate_ids,
        doc_count=len(candidates),
        first_seen=first_seen,
        last_seen=last_seen,
        velocity=velocity,
        dominant_direction=dominant_direction,
        avg_confidence=avg_confidence,
        max_impact=max_impact,
        is_accelerating=is_accelerating,
        is_cross_source=is_cross_source,
        execution_enabled=False,  # I-180
    )


# ── Engine ────────────────────────────────────────────────────────────────────


class NarrativeClusterEngine:
    """Groups SignalCandidates into thematic narrative clusters.

    Algorithm:
    1. Group candidates by NarrativeLabel (from label_map; default: UNKNOWN).
    2. Within each group, sub-cluster by asset Jaccard similarity (greedy).
    3. Filter clusters below min_cluster_size (unless they contain a high-priority signal).
    4. Compute velocity, acceleration, dominant_direction per cluster.
    5. Sort by (doc_count, avg_confidence) descending.
    6. Cap at max_clusters.
    """

    def __init__(self, config: ClusterConfig | None = None) -> None:
        self._config = config or ClusterConfig()

    def cluster(
        self,
        candidates: list[SignalCandidate],
        *,
        label_map: dict[str, NarrativeLabel] | None = None,
        now: datetime | None = None,
    ) -> list[NarrativeCluster]:
        """Cluster candidates into NarrativeClusters.

        Args:
            candidates: SignalCandidates to cluster.
            label_map: Optional mapping of signal_id → NarrativeLabel.
                       Candidates not in label_map receive NarrativeLabel.UNKNOWN.
            now: Reference time for velocity/acceleration. Defaults to UTC now.

        Returns:
            Sorted list of NarrativeClusters (up to max_clusters).
        """
        if not candidates:
            return []

        now = now or datetime.now(UTC)
        lmap = label_map or {}

        # Step 1: assign labels
        by_label: dict[NarrativeLabel, list[SignalCandidate]] = {}
        for cand in candidates:
            label = lmap.get(cand.signal_id, NarrativeLabel.UNKNOWN)
            by_label.setdefault(label, []).append(cand)

        # Step 2–3: sub-cluster + filter
        cluster_counter = itertools.count(1)
        clusters: list[NarrativeCluster] = []

        for label, group in by_label.items():
            sub_groups = self._sub_cluster(group)

            for sub_group in sub_groups:
                # Size filter: skip small clusters unless they contain urgent signals
                if len(sub_group) < self._config.min_cluster_size:
                    if not any(c.priority >= 8 for c in sub_group):
                        continue

                cid = f"{_CLUSTER_ID_PREFIX}-{next(cluster_counter):04d}"
                clusters.append(
                    _build_cluster(
                        cid,
                        label,
                        sub_group,
                        now=now,
                        acceleration_window_hours=self._config.acceleration_window_hours,
                    )
                )

        # Step 4–5: sort
        clusters.sort(key=lambda c: (c.doc_count, c.avg_confidence), reverse=True)

        # Step 6: cap
        return clusters[: self._config.max_clusters]

    def merge_clusters(
        self,
        clusters: list[NarrativeCluster],
        *,
        merge_asset_threshold: float = 0.60,
    ) -> list[NarrativeCluster]:
        """Merge clusters with asset Jaccard overlap ≥ merge_asset_threshold.

        Used for cross-source narrative deduplication.
        Returns clusters sorted by (doc_count, avg_confidence) descending.
        """
        if not clusters:
            return []

        remaining = list(clusters)
        merged: list[NarrativeCluster] = []

        while remaining:
            base = remaining.pop(0)
            base_assets = frozenset(a.upper() for a in base.assets)
            to_merge = [base]
            new_remaining: list[NarrativeCluster] = []

            for other in remaining:
                other_assets = frozenset(a.upper() for a in other.assets)
                if _jaccard(base_assets, other_assets) >= merge_asset_threshold:
                    to_merge.append(other)
                else:
                    new_remaining.append(other)
            remaining = new_remaining

            if len(to_merge) == 1:
                merged.append(base)
            else:
                all_assets = sorted({a for c in to_merge for a in c.assets})[:10]
                all_sources = sorted({s for c in to_merge for s in c.sources})[:10]
                all_cand_ids = [cid for c in to_merge for cid in c.candidate_ids]
                total_docs = sum(c.doc_count for c in to_merge)
                avg_conf = round(
                    sum(c.avg_confidence * c.doc_count for c in to_merge) / total_docs,
                    4,
                )
                max_imp = round(max(c.max_impact for c in to_merge), 4)

                first_vals = [c.first_seen for c in to_merge if c.first_seen]
                last_vals = [c.last_seen for c in to_merge if c.last_seen]
                best = max(to_merge, key=lambda c: c.doc_count)

                dom = _dominant_from_weighted(
                    [c.dominant_direction for c in to_merge],
                    [c.doc_count for c in to_merge],
                )

                merged.append(
                    NarrativeCluster(
                        cluster_id=base.cluster_id,
                        label=base.label,
                        title=best.title,
                        assets=all_assets,
                        entities=[],
                        sources=all_sources,
                        candidate_ids=all_cand_ids,
                        doc_count=total_docs,
                        first_seen=min(first_vals) if first_vals else None,
                        last_seen=max(last_vals) if last_vals else None,
                        velocity=round(sum(c.velocity for c in to_merge), 4),
                        dominant_direction=dom,
                        avg_confidence=avg_conf,
                        max_impact=max_imp,
                        is_accelerating=any(c.is_accelerating for c in to_merge),
                        is_cross_source=len(all_sources) > 1,
                        execution_enabled=False,  # I-180
                    )
                )

        return sorted(merged, key=lambda c: (c.doc_count, c.avg_confidence), reverse=True)

    def _sub_cluster(
        self,
        candidates: list[SignalCandidate],
    ) -> list[list[SignalCandidate]]:
        """Greedy sub-clustering by asset Jaccard similarity."""
        sub_clusters: list[list[SignalCandidate]] = []

        for cand in candidates:
            cand_assets = frozenset(a.upper() for a in cand.affected_assets)
            assigned = False

            for sub in sub_clusters:
                sub_assets = frozenset(a.upper() for c in sub for a in c.affected_assets)
                if _jaccard(cand_assets, sub_assets) >= self._config.merge_threshold:
                    sub.append(cand)
                    assigned = True
                    break

            if not assigned:
                sub_clusters.append([cand])

        return sub_clusters


# ── Persistence ───────────────────────────────────────────────────────────────


def save_narrative_clusters(
    clusters: list[NarrativeCluster],
    output_path: Path | str,
) -> Path:
    """Persist narrative clusters as JSONL. Each line = one cluster (append-safe)."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for cluster in clusters:
            fh.write(json.dumps(cluster.to_json_dict()) + "\n")
    return path
