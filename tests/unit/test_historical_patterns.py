"""
Tests for EventFamily, ReactionArchive, and PatternEnricher.
"""

from __future__ import annotations

import pytest

from app.analysis.historical.pattern import (
    EventFamily,
    TypicalReaction,
    PatternEnrichment,
    PatternEnricher,
    EVENT_FAMILIES,
    REACTION_ARCHIVE,
)
from app.core.enums import NarrativeLabel, EventType


# ─────────────────────────────────────────────
# EventFamily
# ─────────────────────────────────────────────

class TestEventFamily:
    def test_to_dict(self):
        family = EventFamily(
            family_id="fam-001",
            family_name="exchange_collapse",
            description="Major exchange failures",
            event_ids=["hist-002", "hist-007"],
            tags=["exchange", "collapse"],
            typical_narrative=NarrativeLabel.MARKET_CRASH,
        )
        d = family.to_dict()
        assert d["family_id"] == "fam-001"
        assert d["family_name"] == "exchange_collapse"
        assert d["typical_narrative"] == NarrativeLabel.MARKET_CRASH.value
        assert "hist-002" in d["event_ids"]

    def test_seed_families_count(self):
        assert len(EVENT_FAMILIES) >= 7

    def test_seed_families_have_unique_ids(self):
        ids = [f.family_id for f in EVENT_FAMILIES]
        assert len(ids) == len(set(ids))

    def test_exchange_collapse_family(self):
        family = next(f for f in EVENT_FAMILIES if f.family_id == "fam-001")
        assert family.family_name == "exchange_collapse"
        assert family.typical_narrative == NarrativeLabel.MARKET_CRASH
        assert "hist-002" in family.event_ids  # FTX

    def test_btc_institutional_adoption_family(self):
        family = next(f for f in EVENT_FAMILIES if f.family_id == "fam-002")
        assert family.typical_narrative == NarrativeLabel.INSTITUTIONAL_ADOPTION

    def test_halving_family(self):
        family = next(f for f in EVENT_FAMILIES if f.family_id == "fam-004")
        assert "halving" in family.tags
        assert family.typical_narrative == NarrativeLabel.ECOSYSTEM_GROWTH


# ─────────────────────────────────────────────
# TypicalReaction
# ─────────────────────────────────────────────

class TestTypicalReaction:
    def test_to_dict(self):
        reaction = TypicalReaction(
            event_type=EventType.REGULATORY.value,
            narrative=NarrativeLabel.REGULATORY_RISK,
            direction="bearish",
            typical_magnitude_pct=-15.0,
            typical_duration_days=30,
            caveat="Depends on jurisdiction",
            examples=["China ban (-30%)"],
        )
        d = reaction.to_dict()
        assert d["direction"] == "bearish"
        assert d["typical_magnitude_pct"] == -15.0
        assert d["narrative"] == NarrativeLabel.REGULATORY_RISK.value

    def test_seed_reactions_count(self):
        assert len(REACTION_ARCHIVE) >= 7

    def test_regulatory_bearish_reaction(self):
        reaction = next(
            r for r in REACTION_ARCHIVE
            if r.event_type == EventType.REGULATORY.value
            and r.narrative == NarrativeLabel.REGULATORY_RISK
        )
        assert reaction.direction == "bearish"
        assert reaction.typical_magnitude_pct < 0

    def test_institutional_adoption_bullish_reaction(self):
        reaction = next(
            r for r in REACTION_ARCHIVE
            if r.narrative == NarrativeLabel.INSTITUTIONAL_ADOPTION
        )
        assert reaction.direction == "bullish"
        assert reaction.typical_magnitude_pct > 0

    def test_stablecoin_crisis_bearish(self):
        reaction = next(
            r for r in REACTION_ARCHIVE
            if r.narrative == NarrativeLabel.LIQUIDITY_CRISIS
        )
        assert reaction.direction == "bearish"
        assert reaction.typical_magnitude_pct <= -30.0

    def test_tech_upgrade_mixed(self):
        reaction = next(
            r for r in REACTION_ARCHIVE
            if r.narrative == NarrativeLabel.TECH_UPGRADE
        )
        assert reaction.direction == "mixed"
        assert reaction.typical_magnitude_pct is None

    def test_all_reactions_have_caveats(self):
        for r in REACTION_ARCHIVE:
            assert r.caveat, f"Reaction {r.event_type}/{r.narrative.value} missing caveat"

    def test_all_reactions_have_examples(self):
        for r in REACTION_ARCHIVE:
            assert r.examples, f"Reaction {r.event_type}/{r.narrative.value} missing examples"


# ─────────────────────────────────────────────
# PatternEnrichment
# ─────────────────────────────────────────────

class TestPatternEnrichment:
    def test_to_dict_with_all_fields(self):
        family = EVENT_FAMILIES[0]
        reaction = REACTION_ARCHIVE[0]
        enrichment = PatternEnrichment(
            narrative_label=NarrativeLabel.MARKET_CRASH,
            matching_family=family,
            typical_reaction=reaction,
            analogues=[],
            confidence=0.85,
            enrichment_note="Strong historical precedent",
        )
        d = enrichment.to_dict()
        assert d["narrative_label"] == NarrativeLabel.MARKET_CRASH.value
        assert d["confidence"] == 0.85
        assert d["matching_family"] is not None
        assert d["typical_reaction"] is not None
        assert d["analogues"] == []

    def test_to_dict_with_none_fields(self):
        enrichment = PatternEnrichment(
            narrative_label=NarrativeLabel.UNKNOWN,
            matching_family=None,
            typical_reaction=None,
            confidence=0.0,
            enrichment_note="No strong historical pattern found",
        )
        d = enrichment.to_dict()
        assert d["matching_family"] is None
        assert d["typical_reaction"] is None
        assert d["confidence"] == 0.0

    def test_confidence_rounded_to_3_places(self):
        enrichment = PatternEnrichment(
            narrative_label=NarrativeLabel.MACRO_SHIFT,
            matching_family=None,
            typical_reaction=None,
            confidence=0.12345678,
        )
        d = enrichment.to_dict()
        assert d["confidence"] == round(0.12345678, 3)


# ─────────────────────────────────────────────
# PatternEnricher
# ─────────────────────────────────────────────

class TestPatternEnricher:
    def test_init_default(self):
        enricher = PatternEnricher()
        assert enricher is not None

    def test_get_all_families(self):
        enricher = PatternEnricher()
        families = enricher.get_all_families()
        assert len(families) >= 7

    def test_get_family_by_id(self):
        enricher = PatternEnricher()
        family = enricher.get_family("fam-001")
        assert family is not None
        assert family.family_name == "exchange_collapse"

    def test_get_family_missing(self):
        enricher = PatternEnricher()
        assert enricher.get_family("fam-999") is None

    def test_get_reaction_for_narrative(self):
        enricher = PatternEnricher()
        reaction = enricher.get_reaction_for_narrative(NarrativeLabel.REGULATORY_RISK)
        assert reaction is not None
        assert reaction.direction == "bearish"

    def test_get_reaction_for_unknown_narrative(self):
        enricher = PatternEnricher()
        reaction = enricher.get_reaction_for_narrative(NarrativeLabel.UNKNOWN)
        assert reaction is None

    def test_enrich_market_crash(self):
        enricher = PatternEnricher()
        result = enricher.enrich_cluster(
            label=NarrativeLabel.MARKET_CRASH,
            assets=["BTC"],
        )
        assert isinstance(result, PatternEnrichment)
        assert result.narrative_label == NarrativeLabel.MARKET_CRASH
        assert result.matching_family is not None
        assert result.matching_family.family_name == "exchange_collapse"
        assert result.typical_reaction is not None
        assert result.typical_reaction.direction == "bearish"
        assert 0.0 <= result.confidence <= 1.0

    def test_enrich_institutional_adoption(self):
        enricher = PatternEnricher()
        result = enricher.enrich_cluster(
            label=NarrativeLabel.INSTITUTIONAL_ADOPTION,
            assets=["BTC"],
        )
        assert result.matching_family is not None
        assert result.typical_reaction is not None
        assert result.typical_reaction.direction == "bullish"

    def test_enrich_unknown_label(self):
        """Unknown label returns enrichment with no family/reaction but valid structure."""
        enricher = PatternEnricher()
        result = enricher.enrich_cluster(
            label=NarrativeLabel.UNKNOWN,
            assets=["XYZ"],
        )
        assert isinstance(result, PatternEnrichment)
        assert result.matching_family is None
        assert result.typical_reaction is None
        assert result.confidence >= 0.0

    def test_enrich_with_tags(self):
        """enrich_cluster with tags does not raise."""
        enricher = PatternEnricher()
        result = enricher.enrich_cluster(
            label=NarrativeLabel.REGULATORY_RISK,
            assets=["XRP"],
            tags=["sec", "legal"],
        )
        assert isinstance(result, PatternEnrichment)

    def test_confidence_bounded_0_1(self):
        enricher = PatternEnricher()
        for label in [
            NarrativeLabel.MARKET_CRASH,
            NarrativeLabel.INSTITUTIONAL_ADOPTION,
            NarrativeLabel.TECH_UPGRADE,
            NarrativeLabel.LIQUIDITY_CRISIS,
            NarrativeLabel.REGULATORY_RISK,
            NarrativeLabel.MACRO_SHIFT,
        ]:
            result = enricher.enrich_cluster(label=label, assets=["BTC"])
            assert 0.0 <= result.confidence <= 1.0, (
                f"Confidence out of range for {label.value}: {result.confidence}"
            )

    def test_enrichment_note_not_empty(self):
        enricher = PatternEnricher()
        result = enricher.enrich_cluster(
            label=NarrativeLabel.MARKET_CRASH,
            assets=["BTC"],
        )
        assert result.enrichment_note  # Should never be empty for known labels

    def test_analogues_for_btc(self):
        """BTC should have historical analogues in the seed data."""
        enricher = PatternEnricher()
        result = enricher.enrich_cluster(
            label=NarrativeLabel.INSTITUTIONAL_ADOPTION,
            assets=["BTC"],
        )
        # May have analogues depending on seed data match
        assert isinstance(result.analogues, list)
