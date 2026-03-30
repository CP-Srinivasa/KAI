"""Tests for directional alert eligibility guard."""

from __future__ import annotations

from app.alerts.eligibility import (
    BLOCK_REASON_MISSING_ASSETS,
    BLOCK_REASON_UNSUPPORTED_ASSETS,
    BLOCK_REASON_WEAK_SIGNAL,
    MIN_IMPACT_SCORE,
    MIN_SENTIMENT_MAGNITUDE,
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


# ── D-111: Score-strength gates ──────────────────────────────────────────────


def test_weak_sentiment_blocks_directional() -> None:
    """Barely bearish signal (|score| < threshold) must not be directional."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC"],
        sentiment_score=-0.30,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_WEAK_SIGNAL


def test_weak_bullish_sentiment_blocks_directional() -> None:
    """Barely bullish signal below magnitude threshold is blocked."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["ETH"],
        sentiment_score=0.40,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_WEAK_SIGNAL


def test_strong_sentiment_passes_gate() -> None:
    """Strong bearish signal passes the sentiment magnitude gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC"],
        sentiment_score=-0.80,
        impact_score=0.70,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True
    assert "BTC/USDT" in decision.eligible_assets


def test_low_impact_blocks_directional() -> None:
    """High-sentiment but low-impact event is not directional."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["ETH"],
        sentiment_score=0.80,
        impact_score=0.30,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_WEAK_SIGNAL


def test_scores_at_exact_threshold_pass() -> None:
    """Scores exactly at the threshold must pass (>=, not >)."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC"],
        sentiment_score=-MIN_SENTIMENT_MAGNITUDE,
        impact_score=MIN_IMPACT_SCORE,
    )
    assert decision.directional_eligible is True


def test_none_scores_skip_gates() -> None:
    """When scores are None (legacy data), gates are skipped — backwards compat."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
        sentiment_score=None,
        impact_score=None,
    )
    assert decision.directional_eligible is True


def test_sentiment_gate_checked_before_asset_resolution() -> None:
    """Weak signal is blocked before expensive CoinGecko resolution."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC", "ETH", "SOL"],
        sentiment_score=-0.20,
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_WEAK_SIGNAL
    # No eligible_assets populated — gate fires before resolution
    assert decision.eligible_assets == []


def test_neutral_sentiment_unaffected_by_scores() -> None:
    """Non-directional sentiment is not affected by score gates."""
    decision = evaluate_directional_eligibility(
        sentiment_label="neutral",
        affected_assets=["BTC"],
        sentiment_score=0.10,
        impact_score=0.20,
    )
    assert decision.is_directional is False
    assert decision.directional_eligible is None
