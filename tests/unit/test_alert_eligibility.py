"""Tests for directional alert eligibility guard."""

from __future__ import annotations

import pytest

from app.alerts.eligibility import (
    BLOCK_REASON_BEARISH_DISABLED,
    BLOCK_REASON_LOW_PRECISION_SOURCE,
    BLOCK_REASON_LOW_PRIORITY,
    BLOCK_REASON_MISSING_ASSETS,
    BLOCK_REASON_NAKED_ASSET,
    BLOCK_REASON_NOT_ACTIONABLE,
    BLOCK_REASON_PROMO_PATTERN,
    BLOCK_REASON_UNSUPPORTED_ASSETS,
    BLOCK_REASON_WEAK_SIGNAL,
    MIN_IMPACT_SCORE_BULLISH,
    MIN_SENTIMENT_MAGNITUDE,
    _is_promotional,
    _is_reactive_bearish,
    evaluate_directional_eligibility,
)


def test_directional_eligibility_allows_supported_btc_asset() -> None:
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True
    assert decision.eligible_assets == ["BTC/USDT"]
    assert decision.directional_block_reason is None


def test_directional_eligibility_blocks_non_crypto_story_assets() -> None:
    """D-142: bearish is blocked before asset resolution is reached."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["OpenAI", "Disney", "Sora"],
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED


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
    """D-142: Bearish blocked before score gate; weak bullish uses score gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC/USDT"],
        sentiment_score=-0.30,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED


def test_weak_bullish_sentiment_blocks_directional() -> None:
    """Barely bullish signal below magnitude threshold is blocked."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["ETH/USDT"],
        sentiment_score=0.40,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_WEAK_SIGNAL


def test_strong_sentiment_passes_gate() -> None:
    """Strong bullish signal passes the sentiment magnitude gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.80,
        impact_score=0.80,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True
    assert "BTC/USDT" in decision.eligible_assets


def test_low_impact_blocks_directional() -> None:
    """High-sentiment but low-impact event is not directional."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["ETH/USDT"],
        sentiment_score=0.80,
        impact_score=0.30,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_WEAK_SIGNAL


def test_scores_at_exact_threshold_pass() -> None:
    """Scores exactly at the threshold must pass (>=, not >)."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=MIN_SENTIMENT_MAGNITUDE,
        impact_score=MIN_IMPACT_SCORE_BULLISH,
    )
    assert decision.directional_eligible is True


def test_bullish_lower_impact_threshold_passes() -> None:
    """D-121: Bullish uses lower impact threshold than bearish."""
    # Impact at bullish threshold (0.60) passes for bullish...
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.7,
        impact_score=MIN_IMPACT_SCORE_BULLISH,
    )
    assert decision.directional_eligible is True
    # D-142: bearish is now blocked entirely before impact gate is reached
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC/USDT"],
        sentiment_score=-0.7,
        impact_score=MIN_IMPACT_SCORE_BULLISH,
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED


def test_none_scores_skip_gates() -> None:
    """When scores are None (legacy data), gates are skipped — backwards compat."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=None,
        impact_score=None,
    )
    assert decision.directional_eligible is True


def test_sentiment_gate_checked_before_asset_resolution() -> None:
    """D-142: Bearish blocked before expensive CoinGecko resolution."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        sentiment_score=-0.20,
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED
    # No eligible_assets populated — gate fires before resolution
    assert decision.eligible_assets == []


def test_neutral_sentiment_unaffected_by_scores() -> None:
    """Non-directional sentiment is not affected by score gates."""
    decision = evaluate_directional_eligibility(
        sentiment_label="neutral",
        affected_assets=["BTC/USDT"],
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
        "Bitfarms Started Selling All of Its Bitcoin, Pivoting Fully to AI",
        "Google warns quantum computing may break bitcoin earlier than thought",
    ],
)
def test_bearish_blocked_by_d127_before_reactive_filter(title: str) -> None:
    """D-142: All bearish directional is blocked regardless of title pattern."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC/USDT"],
        sentiment_score=-0.80,
        impact_score=0.80,
        title=title,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED


def test_reactive_bearish_filter_only_applies_to_bearish() -> None:
    """Bullish alerts with bearish reactive words are not blocked."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["ETH/USDT"],
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
        affected_assets=["BTC/USDT"],
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
        affected_assets=["BTC/USDT"],
        sentiment_score=0.80,
        impact_score=0.70,
        title=title,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True


def test_reactive_filter_skipped_when_title_is_none() -> None:
    """No title (legacy data) skips the reactive filter — bullish passes."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.80,
        impact_score=0.70,
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


def test_bearish_blocked_before_asset_resolution() -> None:
    """D-142: Bearish blocked before expensive CoinGecko symbol resolution."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"],
        sentiment_score=-0.90,
        impact_score=0.90,
        title="Bitcoin plunges 10% as panic selling hits exchanges",
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED
    # No asset resolution happened — gate fires early
    assert decision.eligible_assets == []


# ── D-122: Actionable gate ──────────────────────────────────────────────────


def test_not_actionable_blocks_directional() -> None:
    """Non-actionable alerts are blocked from directional tracking (D-122)."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        actionable=False,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_NOT_ACTIONABLE


def test_actionable_true_passes_gate() -> None:
    """Actionable alerts pass the actionable gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        actionable=True,
    )
    assert decision.directional_eligible is True


def test_actionable_none_skips_gate() -> None:
    """Legacy data without actionable field skips the gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        actionable=None,
    )
    assert decision.directional_eligible is True


# ── D-122: Low priority gate ────────────────────────────────────────────────


def test_low_priority_blocks_directional() -> None:
    """Priority <= 7 is blocked from directional tracking (D-122)."""
    for pri in (3, 5, 7):
        decision = evaluate_directional_eligibility(
            sentiment_label="bullish",
            affected_assets=["BTC/USDT"],
            priority=pri,
        )
        assert decision.is_directional is True
        assert decision.directional_eligible is False
        assert decision.directional_block_reason == BLOCK_REASON_LOW_PRIORITY


def test_priority_8_passes_gate() -> None:
    """Priority 8 passes the minimum threshold."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        priority=8,
    )
    assert decision.directional_eligible is True


def test_high_priority_passes_gate() -> None:
    """Priority 9 and 10 pass the minimum threshold."""
    for pri in (9, 10):
        decision = evaluate_directional_eligibility(
            sentiment_label="bullish",
            affected_assets=["BTC/USDT"],
            priority=pri,
        )
        assert decision.directional_eligible is True


def test_priority_none_skips_gate() -> None:
    """Legacy data without priority field skips the gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        priority=None,
    )
    assert decision.directional_eligible is True


# ── D-142: Bearish directional disabled ────────────────────────────────────


def test_bearish_directional_disabled() -> None:
    """D-142: Bearish is blocked from directional tracking entirely."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC/USDT"],
        sentiment_score=-0.90,
        impact_score=0.90,
        directional_confidence=0.99,
        priority=10,
        actionable=True,
        title="Major exchange hacked for $500M",
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED


def test_bullish_still_eligible_after_d127() -> None:
    """D-142: Bullish signals are unaffected by bearish block."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.80,
        impact_score=0.70,
        priority=9,
        actionable=True,
        title="BlackRock files for new Bitcoin ETF",
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True
    assert "BTC/USDT" in decision.eligible_assets


# ── D-133: Source-level precision gate ────────────────────────────────────


@pytest.mark.parametrize("source", ["decrypt", "bitcoin_magazine", "unknown"])
def test_low_precision_source_blocks_directional(source: str) -> None:
    """D-133/D-139: Known low-precision sources are blocked.

    D-139 adds ``unknown`` — the fallback used by ``_load_doc_metadata``
    for records whose source cannot be resolved from DB (legacy or purged
    documents).  Empirical precision on unknown: 17.50% (14/66 of 80
    resolved) vs the 60% quality bar.
    """
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.80,
        impact_score=0.70,
        priority=9,
        actionable=True,
        title="BlackRock files for new Bitcoin ETF",
        source_name=source,
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_LOW_PRECISION_SOURCE


def test_low_precision_source_case_insensitive() -> None:
    """D-133: Source name matching is case-insensitive."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        source_name="Decrypt",
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_LOW_PRECISION_SOURCE


def test_good_source_passes_gate() -> None:
    """D-133: Sources not in the blocklist pass the gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.80,
        impact_score=0.70,
        priority=9,
        actionable=True,
        source_name="cointelegraph",
    )
    assert decision.directional_eligible is True


def test_source_none_skips_gate() -> None:
    """D-133: No source_name (legacy data) skips the gate."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        source_name=None,
    )
    assert decision.directional_eligible is True


def test_naked_asset_blocks_directional() -> None:
    """D-xxx: Naked assets without a trading pair format are blocked."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC"],
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_NAKED_ASSET


def test_tradingview_webhook_blocks_directional() -> None:
    """D-xxx: TradingView webhooks are blocked due to 0% precision."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        source_name="tradingview_webhook",
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_LOW_PRECISION_SOURCE


# V-DB4b 2026-05-08: Promotional/listicle filter
@pytest.mark.parametrize(
    "title",
    [
        "Cardano Price Sits 92% Below Its Peak While Pepeto Presale Hits "
        "$9 Million Ahead of Binance Listing",
        "Could the Crypto Price Prediction for Bitcoin and Ethereum Catch Up "
        "to What Pepeto Offers Before Listing",
        "Best Crypto Presale 2026: Pepeto Eyes 100x Before Listing While PEPE and SHIB Trail",
        "Could Pepeto Be One of the Top 3 Cryptos to Buy Now as Solana and Cardano Push Higher",
        "Crypto Update: The Second Chance Entry Pepeto Offers While XRP and Dogecoin Grind",
        "SHIB Burn Frenzy Spikes 812% While Token Targets 100x Potential",
    ],
)
def test_promo_listicle_title_blocked(title: str) -> None:
    """V-DB4b: Promo/Listicle/Pre-Sale-Headlines werden gestoppt vor Score-Gates."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.8,
        impact_score=0.7,
        title=title,
        directional_confidence=0.9,
        actionable=True,
        priority=9,
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_PROMO_PATTERN


@pytest.mark.parametrize(
    "title",
    [
        "Coinbase Q1 Highlights: Double Miss, Market Share Record High",
        "Aptos Foundation, Aptos Labs commit $50M to development",
        "AWS Northern Virginia data center overheats, impacting Coinbase",
        "MicroStrategy buys 2,500 BTC at average price of $89,000",
        "SEC approves new spot Bitcoin ETF application",
        "Hyperliquid Strategies Inc reports $152.5 million net profit",
    ],
)
def test_legit_news_passes_promo_filter(title: str) -> None:
    """Echte Marktnachrichten dürfen NICHT als Promo gefiltert werden."""
    assert _is_promotional(title) is False


def test_is_promotional_helper_explicit() -> None:
    """Direkter Pattern-Match-Test."""
    # Truthy-Cases
    assert _is_promotional("Pepeto Presale Hits $9 Million") is True
    assert _is_promotional("Top 3 Cryptos to Buy in 2026") is True
    assert _is_promotional("100x Before Listing") is True
    assert _is_promotional("Could hit $50,000 by July") is True
    assert _is_promotional("burn frenzy ignites altcoin season") is True
    # Falsy-Cases
    assert _is_promotional("Bitcoin spot ETF inflows hit $245M") is False
    assert _is_promotional("Coinbase reports earnings miss") is False
    assert _is_promotional("Federal Reserve announces rate decision") is False


# V-DB4c 2026-05-08: Source-Watchlist soft confidence adjuster
def test_source_watchlist_modifier_blocks_p8_when_listed(tmp_path, monkeypatch) -> None:
    """Watchlist-Source mit P8 wird durch Modifier zu effektiv P7 → LOW_PRIORITY-Block."""
    from app.alerts import eligibility as elig

    # Schreibe eine Test-Watch-Liste
    watch_file = tmp_path / "source_watch.txt"
    watch_file.write_text("# test\nweakhouse\n")
    monkeypatch.chdir(tmp_path.parent)
    (tmp_path.parent / "monitor").mkdir(exist_ok=True)
    (tmp_path.parent / "monitor" / "source_watch.txt").write_text(
        "# test\nweakhouse\n", encoding="utf-8"
    )
    elig._invalidate_source_watchlist_cache()

    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.8,
        impact_score=0.7,
        title="Some legit news",
        actionable=True,
        priority=8,  # Original P8 → mit Modifier effektiv P7
        source_name="weakhouse",
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_LOW_PRIORITY

    elig._invalidate_source_watchlist_cache()


def test_source_watchlist_modifier_keeps_p9_eligible(tmp_path, monkeypatch) -> None:
    """Watchlist-Source mit P9 wird zu effektiv P8 — eligible, aber tiefer eingestuft."""
    from app.alerts import eligibility as elig

    (tmp_path.parent / "monitor").mkdir(exist_ok=True)
    (tmp_path.parent / "monitor" / "source_watch.txt").write_text(
        "weakhouse\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path.parent)
    elig._invalidate_source_watchlist_cache()

    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.8,
        impact_score=0.7,
        title="Some legit news",
        actionable=True,
        priority=9,  # Original P9 → mit Modifier effektiv P8 → eligible
        source_name="weakhouse",
    )
    # weakhouse ist NICHT in _LOW_PRECISION_SOURCES → kommt durch LOW_PRIORITY
    # mit effective_priority=8 (über P7-Threshold), bleibt eligible.
    assert decision.directional_eligible is True

    elig._invalidate_source_watchlist_cache()


def test_source_watchlist_no_effect_when_not_listed(tmp_path, monkeypatch) -> None:
    """Source nicht auf Watch-Liste → keine Modifizierung."""
    from app.alerts import eligibility as elig

    (tmp_path.parent / "monitor").mkdir(exist_ok=True)
    (tmp_path.parent / "monitor" / "source_watch.txt").write_text(
        "weakhouse\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path.parent)
    elig._invalidate_source_watchlist_cache()

    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.8,
        impact_score=0.7,
        title="Some legit news",
        actionable=True,
        priority=8,
        source_name="cointelegraph",  # NICHT auf Watchlist
    )
    assert decision.directional_eligible is True

    elig._invalidate_source_watchlist_cache()
