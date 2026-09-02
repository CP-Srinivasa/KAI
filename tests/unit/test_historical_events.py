"""Tests for HistoricalEvent domain."""

from datetime import date

import pytest

from app.core.domain.events import EventAnalog, HistoricalEvent

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _btc_halving() -> HistoricalEvent:
    return HistoricalEvent(
        id="btc-halving-2024",
        title="Bitcoin Halving 2024",
        description="BTC block reward halved.",
        event_date=date(2024, 4, 20),
        category="halving",
        sentiment_direction="bullish",
        impact_magnitude=0.85,
        affected_assets=["BTC"],
        affected_sectors=["Mining", "Layer1"],
        tags=["halving", "supply-reduction", "bitcoin"],
    )


def _ftx_crash() -> HistoricalEvent:
    return HistoricalEvent(
        id="ftx-collapse-2022",
        title="FTX Collapse",
        description="FTX exchange collapsed.",
        event_date=date(2022, 11, 11),
        category="crash",
        sentiment_direction="bearish",
        impact_magnitude=0.95,
        affected_assets=["BTC", "ETH", "FTT", "SOL"],
        affected_sectors=["CeFi", "Exchange"],
        tags=["exchange-collapse", "fraud", "contagion"],
    )


# ── HistoricalEvent domain ────────────────────────────────────────────────────


def test_historical_event_fields():
    event = _btc_halving()
    assert event.id == "btc-halving-2024"
    assert event.category == "halving"
    assert event.sentiment_direction == "bullish"
    assert event.impact_magnitude == 0.85
    assert "BTC" in event.affected_assets
    assert "halving" in event.tags


def test_historical_event_impact_clamped():
    with pytest.raises(ValueError):
        HistoricalEvent(
            id="test",
            title="T",
            description="D",
            event_date=date(2024, 1, 1),
            category="other",
            impact_magnitude=1.5,  # invalid
        )


def test_event_analog_fields():
    analog = EventAnalog(
        event_id="btc-halving-2024",
        event_title="Bitcoin Halving 2024",
        similarity_score=0.75,
        matching_reason="Shared assets: BTC",
        shared_assets=["BTC"],
        shared_tags=["halving"],
    )
    assert analog.similarity_score == 0.75
    assert "BTC" in analog.shared_assets
