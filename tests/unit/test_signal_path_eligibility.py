"""WP-B (2026-06-15): per-signal-path eligibility routing.

The narrative gate chain in ``app/alerts/eligibility.py`` is tuned for news/LLM
signals (reactive-title regexes, LLM directional-confidence, news-source
precision tiers, promo filters). Asset-agnostic price/flow signals must NOT be
judged by those gates — ``signal_path="technical"`` bypasses them and applies
only a technical-strength floor, while the asset-resolution safety gates stay
path-independent. These tests pin that contract.
"""

from __future__ import annotations

import pytest

from app.alerts.eligibility import (
    BLOCK_REASON_LOW_DIRECTIONAL_CONFIDENCE,
    BLOCK_REASON_LOW_PRECISION_SOURCE,
    BLOCK_REASON_PROMO_PATTERN,
    BLOCK_REASON_REACTIVE_NARRATIVE,
    BLOCK_REASON_UNSUPPORTED_ASSETS,
    BLOCK_REASON_WEAK_TECHNICAL,
    SIGNAL_PATH_NARRATIVE,
    SIGNAL_PATH_TECHNICAL,
    evaluate_directional_eligibility,
    evaluate_directional_quality_gates,
)

# A symbol that resolves to a supported trading pair (asset gate passes).
_GOOD = "SOL/USDT"


# --------------------------------------------------------------------------- #
# Narrative path: the news-tuned gates STILL fire (control group).
# --------------------------------------------------------------------------- #


def test_narrative_blocks_low_precision_source() -> None:
    """tradingview_webhook is a low-precision NEWS source → blocked on narrative."""
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        source_name="tradingview_webhook",
        priority=9,
        actionable=True,
    )
    assert d.directional_eligible is False
    assert d.directional_block_reason == BLOCK_REASON_LOW_PRECISION_SOURCE


def test_narrative_blocks_reactive_bullish_title() -> None:
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        title="Solana surges 20% in a day",
        priority=9,
        actionable=True,
    )
    assert d.directional_eligible is False
    assert d.directional_block_reason == BLOCK_REASON_REACTIVE_NARRATIVE


# --------------------------------------------------------------------------- #
# Technical path: the narrative gates are BYPASSED (the core WP-B win).
# --------------------------------------------------------------------------- #


def test_technical_bypasses_low_precision_source() -> None:
    """The same tradingview_webhook source is the INTENDED technical source."""
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        source_name="tradingview_webhook",
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.6,
    )
    assert d.directional_eligible is True
    assert d.directional_block_reason is None
    assert d.eligible_assets == [_GOOD]


def test_technical_bypasses_reactive_title() -> None:
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        title="Solana surges 20% in a day",  # would block on narrative
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.6,
    )
    assert d.directional_eligible is True
    assert d.directional_block_reason is None


def test_technical_bypasses_low_confidence() -> None:
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        directional_confidence=0.1,  # far below the 0.8 narrative floor
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.6,
    )
    assert d.directional_eligible is True
    assert d.directional_block_reason != BLOCK_REASON_LOW_DIRECTIONAL_CONFIDENCE


def test_technical_bypasses_promo_title() -> None:
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        title="Top 3 Cryptos To Buy Now before listing",  # promo on narrative
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.6,
    )
    assert d.directional_eligible is True
    assert d.directional_block_reason != BLOCK_REASON_PROMO_PATTERN


# --------------------------------------------------------------------------- #
# Technical-strength gate.
# --------------------------------------------------------------------------- #


def test_technical_strength_below_explicit_threshold_blocks() -> None:
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.3,
        min_technical_strength=0.5,
    )
    assert d.directional_eligible is False
    assert d.directional_block_reason == BLOCK_REASON_WEAK_TECHNICAL


def test_technical_strength_at_threshold_passes() -> None:
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.5,
        min_technical_strength=0.5,
    )
    assert d.directional_eligible is True


def test_technical_strength_none_passes_through() -> None:
    """None strength is consistent with every other optional gate (skip, not block)."""
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=None,
        min_technical_strength=0.9,
    )
    assert d.directional_eligible is True


# --------------------------------------------------------------------------- #
# Path-INDEPENDENT safety gates still apply on the technical path.
# --------------------------------------------------------------------------- #


def test_technical_still_enforces_asset_resolution() -> None:
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["NOTACOIN/USDT"],
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.9,
    )
    assert d.directional_eligible is False
    assert d.directional_block_reason == BLOCK_REASON_UNSUPPORTED_ASSETS


def test_invalid_signal_path_falls_back_to_narrative() -> None:
    """An unknown path must not silently bypass the narrative gates (fail-safe)."""
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        source_name="tradingview_webhook",
        priority=9,
        actionable=True,
        signal_path="bogus_path",
    )
    assert d.directional_eligible is False
    assert d.directional_block_reason == BLOCK_REASON_LOW_PRECISION_SOURCE


@pytest.mark.parametrize("path", [SIGNAL_PATH_NARRATIVE, SIGNAL_PATH_TECHNICAL])
def test_quality_gates_direct_caller_accepts_signal_path(path: str) -> None:
    """The shared quality-gate entrypoint accepts signal_path for both paths."""
    d = evaluate_directional_quality_gates(
        sentiment="bullish",
        affected_assets=[_GOOD],
        signal_path=path,
        technical_strength=0.6,
        directional_confidence=0.9,
        impact_score=0.7,
        sentiment_score=0.8,
        priority=9,
    )
    assert d.directional_eligible is True
