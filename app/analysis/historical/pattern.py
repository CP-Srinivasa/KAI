"""
Historical Pattern Enrichment
==============================
Groups historical events into families and archives known market reactions.

Extends the existing HistoricalMatcher with:
  1. EventFamily    — related events over time (e.g. "exchange_collapse" family)
  2. ReactionArchive — known typical market reactions by event type
  3. PatternEnricher — links NarrativeClusters to historical patterns

Usage:
    enricher = PatternEnricher()
    enrichments = enricher.enrich_cluster(cluster)
    for e in enrichments:
        print(e.family_name, e.typical_reaction, e.confidence)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.enums import EventType, NarrativeLabel
from app.core.logging import get_logger
from app.storage.models.historical import HistoricalEvent
from app.analysis.historical.matcher import SEED_EVENTS, HistoricalMatcher

logger = get_logger(__name__)


# ─────────────────────────────────────────────
# Event Family
# ─────────────────────────────────────────────

@dataclass
class EventFamily:
    """
    A named family of related historical events.
    E.g. "exchange_collapse" contains Mt.Gox, FTX, Celsius.
    """
    family_id: str
    family_name: str
    description: str = ""
    event_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    typical_narrative: NarrativeLabel = NarrativeLabel.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "family_name": self.family_name,
            "description": self.description,
            "event_ids": self.event_ids,
            "tags": self.tags,
            "typical_narrative": self.typical_narrative.value,
        }


# ─────────────────────────────────────────────
# Reaction Archive
# ─────────────────────────────────────────────

@dataclass
class TypicalReaction:
    """
    Known typical market reaction for a given event type/narrative.
    Derived from historical data — not predictive, for context only.
    """
    event_type: str
    narrative: NarrativeLabel
    direction: str                  # "bullish" | "bearish" | "mixed"
    typical_magnitude_pct: float | None = None    # e.g. -20.0 for typical -20%
    typical_duration_days: int | None = None       # typical resolution window
    caveat: str = ""
    examples: list[str] = field(default_factory=list)   # event titles

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "narrative": self.narrative.value,
            "direction": self.direction,
            "typical_magnitude_pct": self.typical_magnitude_pct,
            "typical_duration_days": self.typical_duration_days,
            "caveat": self.caveat,
            "examples": self.examples,
        }


# ─────────────────────────────────────────────
# Seed data
# ─────────────────────────────────────────────

EVENT_FAMILIES: list[EventFamily] = [
    EventFamily(
        family_id="fam-001",
        family_name="exchange_collapse",
        description="Major centralized exchange failures causing market contagion",
        event_ids=["hist-002", "hist-007"],  # FTX, Mt.Gox
        tags=["exchange", "collapse", "contagion", "bear"],
        typical_narrative=NarrativeLabel.MARKET_CRASH,
    ),
    EventFamily(
        family_id="fam-002",
        family_name="btc_institutional_adoption",
        description="Milestone institutional adoption events for Bitcoin",
        event_ids=["hist-001"],  # ETF approval
        tags=["bitcoin_etf", "institutional", "blackrock"],
        typical_narrative=NarrativeLabel.INSTITUTIONAL_ADOPTION,
    ),
    EventFamily(
        family_id="fam-003",
        family_name="stablecoin_crisis",
        description="Stablecoin depeg or collapse events",
        event_ids=["hist-003"],  # Terra/Luna
        tags=["depeg", "stablecoin", "liquidity_crisis"],
        typical_narrative=NarrativeLabel.LIQUIDITY_CRISIS,
    ),
    EventFamily(
        family_id="fam-004",
        family_name="bitcoin_halving_cycle",
        description="Bitcoin block reward halving events",
        event_ids=["hist-004"],  # 2024 halving
        tags=["halving", "supply", "bullish_catalyst"],
        typical_narrative=NarrativeLabel.ECOSYSTEM_GROWTH,
    ),
    EventFamily(
        family_id="fam-005",
        family_name="crypto_regulatory_action",
        description="Regulatory enforcement actions against crypto",
        event_ids=["hist-005"],  # SEC vs Ripple
        tags=["regulatory", "sec", "legal"],
        typical_narrative=NarrativeLabel.REGULATORY_RISK,
    ),
    EventFamily(
        family_id="fam-006",
        family_name="macro_rate_cycle",
        description="Federal Reserve rate policy shifts affecting risk assets",
        event_ids=["hist-006"],
        tags=["fed", "rate", "macro"],
        typical_narrative=NarrativeLabel.MACRO_SHIFT,
    ),
    EventFamily(
        family_id="fam-007",
        family_name="protocol_upgrade",
        description="Major protocol upgrades and network transitions",
        event_ids=["hist-008"],  # ETH Merge
        tags=["upgrade", "fork", "tech"],
        typical_narrative=NarrativeLabel.TECH_UPGRADE,
    ),
]

REACTION_ARCHIVE: list[TypicalReaction] = [
    TypicalReaction(
        event_type=EventType.REGULATORY.value,
        narrative=NarrativeLabel.REGULATORY_RISK,
        direction="bearish",
        typical_magnitude_pct=-15.0,
        typical_duration_days=30,
        caveat="Reaction highly dependent on jurisdiction and asset; positive rulings (SEC vs Ripple) can be bullish.",
        examples=["SEC vs Ripple (+70% XRP)", "China crypto ban (-30% BTC)"],
    ),
    TypicalReaction(
        event_type=EventType.REGULATORY.value,
        narrative=NarrativeLabel.INSTITUTIONAL_ADOPTION,
        direction="bullish",
        typical_magnitude_pct=15.0,
        typical_duration_days=14,
        caveat="ETF and institutional approvals historically bullish but may be pre-priced.",
        examples=["Bitcoin ETF approval Jan 2024 (+15% BTC)"],
    ),
    TypicalReaction(
        event_type=EventType.LEGAL.value,
        narrative=NarrativeLabel.MARKET_CRASH,
        direction="bearish",
        typical_magnitude_pct=-25.0,
        typical_duration_days=90,
        caveat="Exchange collapses cause severe contagion; recovery takes months to years.",
        examples=["FTX collapse (-25% BTC in one week)", "Mt.Gox hack (-36%)"],
    ),
    TypicalReaction(
        event_type=EventType.MARKET_MANIPULATION.value,
        narrative=NarrativeLabel.LIQUIDITY_CRISIS,
        direction="bearish",
        typical_magnitude_pct=-50.0,
        typical_duration_days=180,
        caveat="Stablecoin crises can trigger death spirals and systemic contagion.",
        examples=["Terra/Luna depeg (-80% LUNA, -30% BTC)"],
    ),
    TypicalReaction(
        event_type=EventType.FORK_UPGRADE.value,
        narrative=NarrativeLabel.TECH_UPGRADE,
        direction="mixed",
        typical_magnitude_pct=None,
        typical_duration_days=30,
        caveat="'Buy the rumour, sell the news' pattern common. ETH Merge sold off after.",
        examples=["ETH Merge (-8% initial)", "BTC Halving (varies; historically precedes bull run)"],
    ),
    TypicalReaction(
        event_type=EventType.MACRO_ECONOMIC.value,
        narrative=NarrativeLabel.MACRO_SHIFT,
        direction="bearish",
        typical_magnitude_pct=-20.0,
        typical_duration_days=365,
        caveat="Rate hike cycles correlate with sustained bear markets for risk assets.",
        examples=["Fed rate hike cycle 2022 (BTC -60% over 12 months)"],
    ),
    TypicalReaction(
        event_type=EventType.HACK_EXPLOIT.value,
        narrative=NarrativeLabel.HACK_EXPLOIT,
        direction="bearish",
        typical_magnitude_pct=-10.0,
        typical_duration_days=14,
        caveat="Specific asset affected most; contagion depends on systemic importance.",
        examples=["Mt.Gox hack (-36%)", "Various DeFi exploits (protocol-specific -50-100%)"],
    ),
]


# ─────────────────────────────────────────────
# PatternEnricher
# ─────────────────────────────────────────────

@dataclass
class PatternEnrichment:
    """Result of enriching a narrative cluster with historical patterns."""
    narrative_label: NarrativeLabel
    matching_family: EventFamily | None
    typical_reaction: TypicalReaction | None
    analogues: list[Any] = field(default_factory=list)   # HistoricalAnalogue
    confidence: float = 0.0
    enrichment_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "narrative_label": self.narrative_label.value,
            "matching_family": self.matching_family.to_dict() if self.matching_family else None,
            "typical_reaction": self.typical_reaction.to_dict() if self.typical_reaction else None,
            "analogues": [a.to_dict() for a in self.analogues],
            "confidence": round(self.confidence, 3),
            "enrichment_note": self.enrichment_note,
        }


class PatternEnricher:
    """
    Links NarrativeClusters and signals to historical patterns.
    """

    def __init__(
        self,
        families: list[EventFamily] | None = None,
        reactions: list[TypicalReaction] | None = None,
        events: list[HistoricalEvent] | None = None,
    ) -> None:
        self._families = families or EVENT_FAMILIES
        self._reactions = reactions or REACTION_ARCHIVE
        self._matcher = HistoricalMatcher(events=events or SEED_EVENTS)

        # Build lookup indexes
        self._family_by_narrative: dict[NarrativeLabel, EventFamily] = {}
        for fam in self._families:
            if fam.typical_narrative not in self._family_by_narrative:
                self._family_by_narrative[fam.typical_narrative] = fam

        self._reaction_by_narrative: dict[NarrativeLabel, TypicalReaction] = {}
        for rx in self._reactions:
            if rx.narrative not in self._reaction_by_narrative:
                self._reaction_by_narrative[rx.narrative] = rx

    def enrich_cluster(
        self,
        label: NarrativeLabel,
        assets: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> PatternEnrichment:
        """
        Enrich a narrative cluster with historical family and reaction data.

        Args:
            label:  NarrativeLabel of the cluster
            assets: Assets in the cluster (for analogue search)
            tags:   Tags for context

        Returns:
            PatternEnrichment with family, typical_reaction, analogues.
        """
        family = self._family_by_narrative.get(label)
        reaction = self._reaction_by_narrative.get(label)

        # Find historical analogues
        analogues = self._matcher.find(
            assets=assets,
            tags=tags,
            max_results=3,
        )

        # Confidence: higher if we have all three sources of evidence
        confidence = 0.0
        notes = []
        if family:
            confidence += 0.35
            notes.append(f"Matches event family: {family.family_name}")
        if reaction:
            confidence += 0.35
            notes.append(f"Known reaction: {reaction.direction} ({reaction.typical_magnitude_pct}%)")
        if analogues:
            confidence += 0.30 * analogues[0].similarity_score
            notes.append(f"Historical analogue: {analogues[0].event.title[:50]}")

        logger.debug(
            "pattern_enrichment",
            label=label.value,
            family=family.family_name if family else None,
            analogues=len(analogues),
        )

        return PatternEnrichment(
            narrative_label=label,
            matching_family=family,
            typical_reaction=reaction,
            analogues=analogues,
            confidence=min(1.0, confidence),
            enrichment_note=" | ".join(notes) or "No strong historical pattern found",
        )

    def get_family(self, family_id: str) -> EventFamily | None:
        return next((f for f in self._families if f.family_id == family_id), None)

    def get_all_families(self) -> list[EventFamily]:
        return self._families

    def get_reaction_for_narrative(
        self, label: NarrativeLabel
    ) -> TypicalReaction | None:
        return self._reaction_by_narrative.get(label)
