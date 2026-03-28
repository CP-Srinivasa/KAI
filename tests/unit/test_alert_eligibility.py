"""Tests for directional alert eligibility guard."""

from __future__ import annotations

from app.alerts.eligibility import (
    BLOCK_REASON_MISSING_ASSETS,
    BLOCK_REASON_UNSUPPORTED_ASSETS,
    evaluate_directional_eligibility,
)


def test_directional_eligibility_allows_supported_btc_asset() -> None:
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True
    assert decision.eligible_assets == ["BTC/USDT"]
    assert decision.directional_block_reason is None


def test_directional_eligibility_blocks_non_crypto_story_assets() -> None:
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["OpenAI", "Disney", "Sora"],
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_UNSUPPORTED_ASSETS
    assert decision.blocked_assets == ["OPENAI", "DISNEY", "SORA"]


def test_directional_eligibility_blocks_predictit_and_sports_bill_assets() -> None:
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["PredictIt", "Sports-Bill"],
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_UNSUPPORTED_ASSETS
    assert decision.blocked_assets == ["PREDICTIT", "SPORTS-BILL"]


def test_directional_eligibility_fail_closed_for_unmapped_assets() -> None:
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[],
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_MISSING_ASSETS
    assert decision.eligible_assets == []
