"""Tests for directional alert eligibility guard."""

from __future__ import annotations

import pytest

from app.alerts.eligibility import (
    BLOCK_REASON_LOW_PRIORITY,
    BLOCK_REASON_MISSING_ASSETS,
    BLOCK_REASON_NOT_ACTIONABLE,
    BLOCK_REASON_REACTIVE_NARRATIVE,
    BLOCK_REASON_UNSUPPORTED_ASSETS,
    BLOCK_REASON_WEAK_SIGNAL,
    MIN_IMPACT_SCORE_BEARISH,
    MIN_IMPACT_SCORE_BULLISH,
    MIN_SENTIMENT_MAGNITUDE,
    _is_reactive_bearish,
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
        impact_score=0.80,
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
        impact_score=MIN_IMPACT_SCORE_BEARISH,
    )
    assert decision.directional_eligible is True


def test_bullish_lower_impact_threshold_passes() -> None:
    """D-121: Bullish uses lower impact threshold than bearish."""
    # Impact at bullish threshold (0.60) passes for bullish...
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
        sentiment_score=0.7,
        impact_score=MIN_IMPACT_SCORE_BULLISH,
    )
    assert decision.directional_eligible is True
    # ...but same score blocks bearish (needs 0.75)
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC"],
        sentiment_score=-0.7,
        impact_score=MIN_IMPACT_SCORE_BULLISH,
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_WEAK_SIGNAL


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


# ── D-113: Reactive price narrative gate ────────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "Bitcoin drops toward $65k after new Trump Iran delay sends oil higher",
        "Bitcoin Dips Under $67K as Geopolitical Uncertainty Spooks Traders",
        "Bitcoin Price Slides to Two-Week Low as Liquidations Top $300M",
        "Bitcoin price just collapsed because the macro selloff collided",
        "Strategy and Robinhood Shares Hit Monthly Lows as Bitcoin Sinks",
        "Bitcoin has to survive a new major liquidity test — $2.2B wipeout",
        "Bitcoin drops as Rubio privately signals Iran war may last weeks",
        "Bitcoin Fear and Greed Index Hits Extreme Fear at 13 Out of 100",
        "US Bitcoin ETFs see $296 million weekly outflows",
        "As Bitcoin weakens even safe investments are starting to crack",
        "BTC plunges below support as sell-off accelerates",
        "Ethereum tumbles 8% in broad crypto crash",
        "Crypto markets fall sharply amid rising yields",
        "Bitcoin heading for weekend collapse to $61k",
    ],
)
def test_reactive_bearish_title_blocked(title: str) -> None:
    """Bearish alert with reactive price narrative title is blocked (D-113)."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC"],
        sentiment_score=-0.80,
        impact_score=0.80,
        title=title,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_REACTIVE_NARRATIVE


@pytest.mark.parametrize(
    "title",
    [
        "Bitfarms Started Selling All of Its Bitcoin, Pivoting Fully to AI",
        "Bitcoin treasury company sells $20M BTC at a loss",
        "Bitmine scoops $147M of ETH in its biggest week of buying",
        "Interactive Brokers launches crypto trading in EEA",
        "KuCoin operator ordered to block US traders, pay $500K penalty",
        "Google warns quantum computing may break bitcoin earlier than thought",
    ],
)
def test_actor_action_bearish_title_allowed(title: str) -> None:
    """Bearish alert with actor-action title passes reactive filter (D-113)."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC"],
        sentiment_score=-0.80,
        impact_score=0.80,
        title=title,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True


def test_reactive_bearish_filter_only_applies_to_bearish() -> None:
    """Bullish alerts with bearish reactive words are not blocked."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["ETH"],
        sentiment_score=0.80,
        impact_score=0.70,
        title="Ethereum drops to key support then bounces hard",
    )
    assert decision.directional_eligible is True


@pytest.mark.parametrize(
    "title",
    [
        "Bitcoin surges past $100K on ETF optimism",
        "ETH rallies 15% in surprise breakout session",
        "Crypto market soars as Fed signals rate cuts",
        "Bitcoin jumps to monthly high after whale buying",
        "BTC spikes above resistance on massive volume",
        "Bitcoin rockets through key level as bulls take control",
        "Ethereum price exploding as DeFi TVL hits new ATH",
        "Bitcoin hits new all-time high above $120K",
        "Massive ETF inflows push BTC higher",
        "SOL breaking out as Solana ecosystem surges",
    ],
)
def test_reactive_bullish_title_blocked(title: str) -> None:
    """Bullish alert with reactive price-movement title is blocked (D-115)."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
        sentiment_score=0.80,
        impact_score=0.70,
        title=title,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == "reactive_price_narrative"


@pytest.mark.parametrize(
    "title",
    [
        "MicroStrategy buys $500M of Bitcoin in latest purchase",
        "BlackRock files for Ethereum ETF approval",
        "Google warns quantum computing may break bitcoin earlier than thought",
        "Fidelity adds Bitcoin to retirement accounts",
    ],
)
def test_actor_action_bullish_title_allowed(title: str) -> None:
    """Bullish alert with actor-action title passes reactive filter."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
        sentiment_score=0.80,
        impact_score=0.70,
        title=title,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True


def test_reactive_filter_skipped_when_title_is_none() -> None:
    """No title (legacy data) skips the reactive filter."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC"],
        sentiment_score=-0.80,
        impact_score=0.80,
        title=None,
    )
    assert decision.directional_eligible is True


def test_is_reactive_bearish_helper() -> None:
    """Direct test of the pattern matcher."""
    assert _is_reactive_bearish("Bitcoin drops below $60K") is True
    assert _is_reactive_bearish("Price slides to two-week low") is True
    assert _is_reactive_bearish("Extreme fear grips the market") is True


def test_is_reactive_bullish_helper() -> None:
    """Direct test of the bullish pattern matcher."""
    from app.alerts.eligibility import _is_reactive_bullish

    assert _is_reactive_bullish("Bitcoin surges past $100K") is True
    assert _is_reactive_bullish("ETH rallies on ETF news") is True
    assert _is_reactive_bullish("BTC hits all-time high") is True
    assert _is_reactive_bullish("MicroStrategy buys more Bitcoin") is False
    assert _is_reactive_bullish("Fed cuts rates") is False
    assert _is_reactive_bearish("Bitfarms sells all BTC holdings") is False
    assert _is_reactive_bearish("SEC approves new ETF application") is False


def test_reactive_filter_fires_before_asset_resolution() -> None:
    """Reactive gate fires before expensive CoinGecko symbol resolution."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC", "ETH", "SOL", "XRP"],
        sentiment_score=-0.90,
        impact_score=0.90,
        title="Bitcoin plunges 10% as panic selling hits exchanges",
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_REACTIVE_NARRATIVE
    # No asset resolution happened — gate fires early
    assert decision.eligible_assets == []


# ── D-122: Actionable gate ──────────────────────────────────────────────────


def test_not_actionable_blocks_directional() -> None:
    """Non-actionable alerts are blocked from directional tracking (D-122)."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
        actionable=False,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_NOT_ACTIONABLE


def test_actionable_true_passes_gate() -> None:
    """Actionable alerts pass the actionable gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
        actionable=True,
    )
    assert decision.directional_eligible is True


def test_actionable_none_skips_gate() -> None:
    """Legacy data without actionable field skips the gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
        actionable=None,
    )
    assert decision.directional_eligible is True


# ── D-122: Low priority gate ────────────────────────────────────────────────


def test_low_priority_blocks_directional() -> None:
    """Priority <= 7 is blocked from directional tracking (D-122)."""
    for pri in (3, 5, 7):
        decision = evaluate_directional_eligibility(
            sentiment_label="bullish",
            affected_assets=["BTC"],
            priority=pri,
        )
        assert decision.is_directional is True
        assert decision.directional_eligible is False
        assert decision.directional_block_reason == BLOCK_REASON_LOW_PRIORITY


def test_priority_8_passes_gate() -> None:
    """Priority 8 passes the minimum threshold."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
        priority=8,
    )
    assert decision.directional_eligible is True


def test_high_priority_passes_gate() -> None:
    """Priority 9 and 10 pass the minimum threshold."""
    for pri in (9, 10):
        decision = evaluate_directional_eligibility(
            sentiment_label="bullish",
            affected_assets=["BTC"],
            priority=pri,
        )
        assert decision.directional_eligible is True


def test_priority_none_skips_gate() -> None:
    """Legacy data without priority field skips the gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
        priority=None,
    )
    assert decision.directional_eligible is True
