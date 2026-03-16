"""
Research Pack Builder
======================
Assembles ResearchPack models from collections of SignalCandidates.

All methods are pure (no IO, no DB) — they take in-memory data and return
structured pack objects ready for serialisation.

Usage:
    builder = ResearchPackBuilder()
    asset_pack = builder.for_asset("BTC", candidates)
    narrative_pack = builder.for_narrative(NarrativeLabel.REGULATORY_RISK, candidates)
    brief = builder.daily_brief(candidates, date="2024-01-15")
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from app.core.enums import DirectionHint, NarrativeLabel, SignalUrgency
from app.core.logging import get_logger
from app.research.models import (
    AssetResearchPack,
    BreakingNewsPack,
    DailyResearchBrief,
    NarrativePack,
    SignalSummary,
)
from app.trading.signals.candidate import SignalCandidate

logger = get_logger(__name__)

_URGENCY_RANK = {
    SignalUrgency.IMMEDIATE: 5,
    SignalUrgency.SHORT_TERM: 4,
    SignalUrgency.MEDIUM_TERM: 3,
    SignalUrgency.LONG_TERM: 2,
    SignalUrgency.MONITOR: 1,
}


def _consensus_direction(candidates: list[SignalCandidate]) -> DirectionHint:
    counts: Counter[str] = Counter(c.direction_hint.value for c in candidates)
    if not counts:
        return DirectionHint.NEUTRAL
    top, second = (counts.most_common(2) + [("", 0), ("", 0)])[:2]
    # Require clear majority (>50%) for BULLISH/BEARISH; else MIXED
    total = sum(counts.values())
    if top[0] in ("bullish", "bearish") and top[1] / total > 0.5:
        return DirectionHint(top[0])
    if top[0] == "neutral":
        return DirectionHint.NEUTRAL
    return DirectionHint.MIXED


def _max_urgency(candidates: list[SignalCandidate]) -> SignalUrgency:
    if not candidates:
        return SignalUrgency.MONITOR
    return max(candidates, key=lambda c: _URGENCY_RANK[c.urgency]).urgency


def _avg_confidence(candidates: list[SignalCandidate]) -> float:
    if not candidates:
        return 0.0
    return round(sum(c.confidence for c in candidates) / len(candidates), 3)


def _to_summary(c: SignalCandidate) -> SignalSummary:
    return SignalSummary(
        signal_id=c.id,
        asset=c.asset,
        direction_hint=c.direction_hint.value,
        confidence=round(c.confidence, 3),
        urgency=c.urgency.value,
        narrative_label=c.narrative_label.value,
        title=c.title,
        recommended_next_step=c.recommended_next_step,
        risk_notes=c.risk_notes,
    )


class ResearchPackBuilder:
    """Assembles structured research packs from signal candidates."""

    # ── Asset Pack ────────────────────────────────────────────────────────

    def for_asset(
        self,
        symbol: str,
        candidates: list[SignalCandidate],
        max_evidence: int = 5,
    ) -> AssetResearchPack:
        """Build a research pack for a single asset."""
        relevant = [c for c in candidates if c.asset == symbol]
        relevant_sorted = sorted(relevant, key=lambda c: c.confidence, reverse=True)

        # Collect evidence (deduplicated)
        supporting: list[str] = []
        contradicting: list[str] = []
        risk_notes: list[str] = []
        sources: list[str] = []
        for c in relevant_sorted:
            supporting += [e for e in c.supporting_evidence if e not in supporting]
            contradicting += [e for e in c.contradicting_evidence if e not in contradicting]
            risk_notes += [r for r in c.risk_notes if r not in risk_notes]
            if c.source_id and c.source_id not in sources:
                sources.append(c.source_id)

        narrative_labels = list(
            dict.fromkeys(c.narrative_label.value for c in relevant_sorted)
        )

        pack = AssetResearchPack(
            asset=symbol,
            direction_consensus=_consensus_direction(relevant),
            overall_confidence=_avg_confidence(relevant),
            signals=[_to_summary(c) for c in relevant_sorted[:10]],
            top_supporting_evidence=supporting[:max_evidence],
            top_contradicting_evidence=contradicting[:max_evidence],
            key_risk_notes=risk_notes[:max_evidence],
            narrative_labels=narrative_labels[:5],
            urgency=_max_urgency(relevant),
            total_documents=len(relevant),
            sources=sources[:10],
        )
        logger.debug("asset_pack_built", asset=symbol, signals=len(relevant))
        return pack

    # ── Narrative Pack ─────────────────────────────────────────────────────

    def for_narrative(
        self,
        label: NarrativeLabel,
        candidates: list[SignalCandidate],
    ) -> NarrativePack:
        """Build a research pack for a specific narrative."""
        relevant = [c for c in candidates if c.narrative_label == label]
        relevant_sorted = sorted(relevant, key=lambda c: c.confidence, reverse=True)

        affected_assets = list(dict.fromkeys(c.asset for c in relevant_sorted))
        risk_notes = list(
            dict.fromkeys(r for c in relevant_sorted for r in c.risk_notes)
        )

        # Build summary from top signal title
        title = relevant_sorted[0].title if relevant_sorted else ""
        summary = (
            f"{len(relevant)} signal(s) linked to narrative '{label.value}'. "
            f"Affected assets: {', '.join(affected_assets[:5])}."
        )

        pack = NarrativePack(
            narrative_label=label,
            title=title,
            summary=summary,
            affected_assets=affected_assets[:10],
            signals=[_to_summary(c) for c in relevant_sorted[:10]],
            overall_confidence=_avg_confidence(relevant),
            dominant_direction=_consensus_direction(relevant),
            urgency=_max_urgency(relevant),
            risk_notes=risk_notes[:5],
        )
        logger.debug("narrative_pack_built", label=label.value, signals=len(relevant))
        return pack

    # ── Breaking Cluster ───────────────────────────────────────────────────

    def for_breaking_cluster(
        self,
        candidates: list[SignalCandidate],
        cluster_title: str = "",
    ) -> BreakingNewsPack:
        """Build a breaking news cluster pack from a set of high-urgency candidates."""
        sorted_c = sorted(candidates, key=lambda c: c.confidence, reverse=True)
        affected_assets = list(dict.fromkeys(c.asset for c in sorted_c))
        sources = list(dict.fromkeys(c.source_id for c in sorted_c if c.source_id))
        key_facts = list(dict.fromkeys(c.title for c in sorted_c if c.title))[:5]
        risk_notes = list(dict.fromkeys(r for c in sorted_c for r in c.risk_notes))[:5]
        max_impact = max((c.impact_score for c in candidates), default=0.0)

        title = cluster_title or (sorted_c[0].title if sorted_c else "Breaking Cluster")

        pack = BreakingNewsPack(
            cluster_title=title,
            key_facts=key_facts,
            affected_assets=affected_assets[:10],
            signals=[_to_summary(c) for c in sorted_c[:8]],
            sources=sources[:10],
            max_impact_score=max_impact,
            urgency=_max_urgency(candidates),
            direction_hint=_consensus_direction(candidates),
            risk_notes=risk_notes,
            document_count=len(candidates),
        )
        logger.debug("breaking_cluster_built", assets=len(affected_assets), docs=len(candidates))
        return pack

    # ── Daily Brief ────────────────────────────────────────────────────────

    def daily_brief(
        self,
        candidates: list[SignalCandidate],
        date: str = "",
        max_assets: int = 6,
        max_narratives: int = 4,
    ) -> DailyResearchBrief:
        """Build a full daily research brief from all signal candidates."""
        if not date:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        if not candidates:
            return DailyResearchBrief(
                date=date,
                total_signals=0,
                market_sentiment="neutral",
                overall_urgency=SignalUrgency.MONITOR,
            )

        # Group candidates by asset
        by_asset: dict[str, list[SignalCandidate]] = defaultdict(list)
        for c in candidates:
            by_asset[c.asset].append(c)

        # Build per-asset packs, ranked by avg confidence
        asset_packs = [
            self.for_asset(symbol, asset_candidates)
            for symbol, asset_candidates in by_asset.items()
        ]
        asset_packs.sort(key=lambda p: p.overall_confidence, reverse=True)
        top_assets = asset_packs[:max_assets]

        # Build narrative packs
        by_narrative: dict[NarrativeLabel, list[SignalCandidate]] = defaultdict(list)
        for c in candidates:
            by_narrative[c.narrative_label].append(c)

        narrative_packs = [
            self.for_narrative(label, nc)
            for label, nc in by_narrative.items()
            if label != NarrativeLabel.UNKNOWN and len(nc) >= 2
        ]
        narrative_packs.sort(key=lambda p: p.overall_confidence, reverse=True)
        active_narratives = narrative_packs[:max_narratives]

        # Breaking clusters: high-urgency candidates grouped by asset
        urgent = [
            c for c in candidates
            if c.urgency in (SignalUrgency.IMMEDIATE, SignalUrgency.SHORT_TERM)
        ]
        breaking_clusters: list[BreakingNewsPack] = []
        if urgent:
            breaking_clusters.append(
                self.for_breaking_cluster(urgent, "Breaking Signals Cluster")
            )

        # Aggregate metrics
        direction_counts: Counter[str] = Counter(
            c.direction_hint.value for c in candidates
        )
        total = len(candidates)
        bullish_ratio = direction_counts.get("bullish", 0) / total
        bearish_ratio = direction_counts.get("bearish", 0) / total
        if bullish_ratio > 0.55:
            market_sentiment = "positive"
        elif bearish_ratio > 0.55:
            market_sentiment = "negative"
        else:
            market_sentiment = "neutral"

        key_themes = list(
            dict.fromkeys(
                label
                for pack in active_narratives
                for label in [pack.narrative_label]
            )
        )

        risk_summary = list(
            dict.fromkeys(
                note
                for pack in top_assets
                for note in pack.key_risk_notes
            )
        )[:6]

        watchlist_hits = [p.asset for p in asset_packs if p.total_documents >= 1][:10]

        brief = DailyResearchBrief(
            date=date,
            total_signals=total,
            top_assets=top_assets,
            active_narratives=active_narratives,
            breaking_clusters=breaking_clusters,
            market_sentiment=market_sentiment,
            overall_urgency=_max_urgency(candidates),
            key_themes=key_themes,
            risk_summary=risk_summary,
            watchlist_hits=watchlist_hits,
        )
        logger.info(
            "daily_brief_built",
            date=date,
            total_signals=total,
            assets=len(top_assets),
            narratives=len(active_narratives),
        )
        return brief
