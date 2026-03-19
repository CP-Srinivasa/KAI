"""Tests for HistoricalEvent domain, EventAnalogMatcher, and validation helpers."""

from datetime import date
from pathlib import Path

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.analysis.historical.matcher import EventAnalogMatcher, _score_event
from app.analysis.validation import sanitize_scores, validate_llm_output
from app.core.domain.events import EventAnalog, HistoricalEvent
from app.core.enums import MarketScope, SentimentLabel

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


def _make_llm_output(**overrides) -> LLMAnalysisOutput:
    defaults = {
        "sentiment_label": SentimentLabel.BULLISH,
        "sentiment_score": 0.7,
        "relevance_score": 0.8,
        "impact_score": 0.6,
        "confidence_score": 0.9,
        "novelty_score": 0.5,
        "spam_probability": 0.05,
        "market_scope": MarketScope.CRYPTO,
        "recommended_priority": 6,
        "actionable": False,
    }
    defaults.update(overrides)
    return LLMAnalysisOutput(**defaults)


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


# ── EventAnalogMatcher ────────────────────────────────────────────────────────


def test_matcher_finds_halving_analog():
    matcher = EventAnalogMatcher([_btc_halving(), _ftx_crash()])
    analogs = matcher.find_analogs(
        assets=["BTC"],
        tags=["halving", "supply-reduction"],
        event_type="halving",
    )
    assert len(analogs) >= 1
    assert analogs[0].event_id == "btc-halving-2024"
    assert analogs[0].similarity_score > 0.0
    assert "btc" in analogs[0].shared_assets


def test_matcher_no_overlap_returns_empty():
    matcher = EventAnalogMatcher([_btc_halving()])
    analogs = matcher.find_analogs(assets=["DOGE"], tags=["meme"], min_score=0.5)
    assert analogs == []


def test_matcher_top_n_limit():
    events = [_btc_halving(), _ftx_crash()]
    matcher = EventAnalogMatcher(events)
    analogs = matcher.find_analogs(assets=["BTC", "ETH"], tags=["halving", "contagion"], top_n=1)
    assert len(analogs) <= 1


def test_matcher_results_sorted_descending():
    matcher = EventAnalogMatcher([_btc_halving(), _ftx_crash()])
    analogs = matcher.find_analogs(
        assets=["BTC", "ETH"],
        tags=["halving", "exchange-collapse", "fraud"],
        min_score=0.01,
        top_n=10,
    )
    scores = [a.similarity_score for a in analogs]
    assert scores == sorted(scores, reverse=True)


def test_matcher_from_monitor_dir(tmp_path: Path):
    yaml_content = """
events:
  - id: test-event
    title: Test Event
    description: A test event.
    event_date: "2023-01-01"
    category: other
    sentiment_direction: neutral
    impact_magnitude: 0.5
    affected_assets: [BTC]
    affected_sectors: []
    tags: [test, bitcoin]
"""
    (tmp_path / "historical_events.yml").write_text(yaml_content)
    matcher = EventAnalogMatcher.from_monitor_dir(tmp_path)
    analogs = matcher.find_analogs(assets=["BTC"], tags=["test"])
    assert len(analogs) == 1
    assert analogs[0].event_id == "test-event"


def test_matcher_from_monitor_dir_missing_file(tmp_path: Path):
    matcher = EventAnalogMatcher.from_monitor_dir(tmp_path)
    assert matcher.find_analogs(assets=["BTC"], tags=[]) == []


def test_score_event_perfect_asset_match():
    event = _btc_halving()
    score, shared_assets, shared_tags = _score_event(event, {"btc"}, set(), None)
    assert score > 0
    assert "btc" in shared_assets


def test_score_event_category_bonus():
    event = _btc_halving()
    score_with, _, _ = _score_event(event, {"btc"}, set(), "halving")
    score_without, _, _ = _score_event(event, {"btc"}, set(), None)
    assert score_with > score_without


# ── validate_llm_output ───────────────────────────────────────────────────────


def test_validate_clean_output_no_warnings():
    output = _make_llm_output()
    warnings = validate_llm_output(output)
    assert warnings == []


def test_validate_bullish_negative_score():
    output = _make_llm_output(sentiment_label=SentimentLabel.BULLISH, sentiment_score=-0.5)
    warnings = validate_llm_output(output)
    assert any("BULLISH" in w for w in warnings)


def test_validate_bearish_positive_score():
    output = _make_llm_output(sentiment_label=SentimentLabel.BEARISH, sentiment_score=0.5)
    warnings = validate_llm_output(output)
    assert any("BEARISH" in w for w in warnings)


def test_validate_spam_high_priority():
    output = _make_llm_output(spam_probability=0.9, recommended_priority=8)
    warnings = validate_llm_output(output)
    assert any("spam" in w.lower() for w in warnings)


def test_validate_actionable_low_relevance():
    output = _make_llm_output(actionable=True, relevance_score=0.1)
    warnings = validate_llm_output(output)
    assert any("actionable" in w.lower() for w in warnings)


def test_validate_high_priority_no_reasoning():
    output = _make_llm_output(recommended_priority=9, short_reasoning=None)
    warnings = validate_llm_output(output)
    assert any("reasoning" in w.lower() for w in warnings)


# ── sanitize_scores ───────────────────────────────────────────────────────────


def test_sanitize_clamps_scores():
    # Bypass Pydantic validation by using model_copy
    output = _make_llm_output()
    dirty = output.model_copy(
        update={
            "sentiment_score": 1.5,
            "relevance_score": -0.1,
            "recommended_priority": 15,
        }
    )
    clean = sanitize_scores(dirty)
    assert clean.sentiment_score == 1.0
    assert clean.relevance_score == 0.0
    assert clean.recommended_priority == 10


def test_sanitize_valid_output_unchanged():
    output = _make_llm_output()
    clean = sanitize_scores(output)
    assert clean.sentiment_score == output.sentiment_score
    assert clean.relevance_score == output.relevance_score
    assert clean.recommended_priority == output.recommended_priority
