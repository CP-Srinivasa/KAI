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
        # V-DB5 Calibration 2026-05-08 (audit B-A2):
        # "Massive ETF inflows push BTC higher" wurde entfernt — ETF-Inflows sind
        # Substanz-Events (Capital-Flow), keine reaktiven Preis-Narrative. Der
        # \\binflows?\\b-Filter im REACTIVE_BULLISH-Set wurde entfernt; siehe
        # auch test_etf_inflows_passes_reactive_filter unten.
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


# V-DB4b 2026-05-08 + V-DB5 Calibration: Promotional/listicle filter
@pytest.mark.parametrize(
    "title",
    [
        # Pre-Sale-Familie (immer Promo)
        (
            "Cardano Price Sits 92% Below Its Peak While Pepeto Presale Hits "
            "$9 Million Ahead of Binance Listing"
        ),
        "Best Crypto Presale 2026: Pepeto Eyes 100x Before Listing While PEPE and SHIB Trail",
        "Pre-sale opens Tuesday — limited allocation",
        # Listicle-Marker
        "Could Pepeto Be One of the Top 3 Cryptos to Buy Now as Solana and Cardano Push Higher",
        "Top 5 Coins To Buy This Week",
        # Catch-up / Second-Chance Pump-Phrasen
        "Crypto Update: The Second Chance Entry Pepeto Offers in Q2",
        (
            "Could the Crypto Price Prediction for Bitcoin and Ethereum Catch Up "
            "to What Pepeto Offers Before Listing"
        ),
        # Multiplikator-Ziele MIT Promo-Substanz (V-DB5: enger gefasst)
        "SHIB Targets 100x Gains Before Listing",
        "PEPE eyes 200x return in coming weeks",
        # Could-hit MIT Zeit-Anker (V-DB5: enger gefasst)
        "Bitcoin could hit $200,000 by December",
        "ETH could hit $10000 by Q3",
        # Burn frenzy (Pump-Hype ohne Substanz)
        "SHIB Burn Frenzy Ignites Altcoin Season",
    ],
)
def test_promo_listicle_title_blocked(title: str) -> None:
    """V-DB4b/V-DB5: Promo/Listicle/Pre-Sale-Headlines werden gestoppt vor Score-Gates."""
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
        # Mainstream-Marktnachrichten (heute zentral)
        "Coinbase Q1 Highlights: Double Miss, Market Share Record High",
        "Aptos Foundation, Aptos Labs commit $50M to development",
        "AWS Northern Virginia data center overheats, impacting Coinbase",
        "MicroStrategy buys 2,500 BTC at average price of $89,000",
        "SEC approves new spot Bitcoin ETF application",
        "Hyperliquid Strategies Inc reports $152.5 million net profit",
        # V-DB5 Calibration: vorher fälschlich als Promo geblockt
        # C-1: "price prediction" allein ist Mainstream-SEO
        "Bitcoin Price Prediction by Standard Chartered: $250K Target",
        "ETH Price Prediction by Cathie Wood",
        "Solana price prediction hit list",
        # C-2: "targets/eyes Nx" ohne Promo-Trailer ist legit
        "Trump targets 200x export tariffs on Chinese chips",
        "Visa eyes 1000x scaling target",
        "BTC eyes 100x cycle target",
        "Solana 100x in three years study finds",
        # F-003: "could hit $X — analysts split" ohne Zeit-Anker ist Analyst-Konsens
        "Bitcoin could hit $80,000 — analysts split on timing",
        "Bitcoin could hit $100,000",
        # C-3: "offers while ... and ..." ist normales Englisch
        "Robinhood offers while Coinbase grinds out gains",
        "Issuer offers while ETF and SEC negotiate",
    ],
)
def test_legit_news_passes_promo_filter(title: str) -> None:
    """Echte Marktnachrichten dürfen NICHT als Promo gefiltert werden."""
    assert _is_promotional(title) is False


def test_is_promotional_helper_explicit() -> None:
    """Direkter Pattern-Match-Test (V-DB5 calibrated)."""
    # Truthy-Cases (Promo)
    assert _is_promotional("Pepeto Presale Hits $9 Million") is True
    assert _is_promotional("Top 3 Cryptos to Buy in 2026") is True
    assert _is_promotional("100x potential within weeks") is True
    assert _is_promotional("Could hit $50,000 by July") is True
    assert _is_promotional("burn frenzy ignites altcoin season") is True
    assert _is_promotional("eyes 1000x return") is True
    assert _is_promotional("100x before listing") is True
    # Falsy-Cases (legit)
    assert _is_promotional("Bitcoin spot ETF inflows hit $245M") is False
    assert _is_promotional("Coinbase reports earnings miss") is False
    assert _is_promotional("Federal Reserve announces rate decision") is False
    assert _is_promotional("Bitcoin Price Prediction by Standard Chartered") is False
    assert _is_promotional("Trump targets 200x export tariffs") is False
    assert _is_promotional("Robinhood offers while Coinbase grinds") is False


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
    (tmp_path.parent / "monitor" / "source_watch.txt").write_text("weakhouse\n", encoding="utf-8")
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
    (tmp_path.parent / "monitor" / "source_watch.txt").write_text("weakhouse\n", encoding="utf-8")
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


def test_source_watchlist_reloads_on_mtime_change(tmp_path, monkeypatch) -> None:
    """V-DB5: Watchlist-File-Edit wird ohne Worker-Restart wirksam (mtime-Reload)."""
    import time

    from app.alerts import eligibility as elig

    (tmp_path.parent / "monitor").mkdir(exist_ok=True)
    watch_file = tmp_path.parent / "monitor" / "source_watch.txt"
    watch_file.write_text("source_alpha\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    elig._invalidate_source_watchlist_cache()

    # Erste Lektüre — alpha auf Liste
    assert "source_alpha" in elig._load_source_watchlist()
    assert "source_beta" not in elig._load_source_watchlist()

    # Operator editiert das File: alpha raus, beta rein
    time.sleep(0.05)  # mtime-Resolution kann grob sein
    watch_file.write_text("source_beta\n", encoding="utf-8")
    # Force mtime change explizit (Windows-Fallback)
    import os

    new_mtime = watch_file.stat().st_mtime + 1
    os.utime(watch_file, (new_mtime, new_mtime))

    # Reload OHNE Cache-Invalidate-Aufruf → mtime-basiert
    after = elig._load_source_watchlist()
    assert "source_beta" in after
    assert "source_alpha" not in after

    elig._invalidate_source_watchlist_cache()


def test_source_watchlist_missing_file_returns_empty(tmp_path, monkeypatch) -> None:
    """File fehlt → frozenset() ohne Crash."""
    from app.alerts import eligibility as elig

    monkeypatch.chdir(tmp_path)  # tmp_path enthält kein monitor/
    elig._invalidate_source_watchlist_cache()
    assert elig._load_source_watchlist() == frozenset()
    elig._invalidate_source_watchlist_cache()


# ============================================================================
# F1 (Sprint 2026-05-24) — Substantive-Trigger Whitelist for reactive patterns.
#
# Befund-Memo: artifacts/operator_memos/dispatch_filter_root_befund_2026-05-24.md
# Memory: kai-dispatch-filter-root-befund-20260524
#
# These tests cover: (a) the new `_has_substantive_trigger` helper,
# (b) Whitelist-Override für reaktive Patterns in _is_reactive_bullish/bearish,
# (c) Replay-Cases aus 14d-Sample blocked_alerts.jsonl 10.-24.05.2026.
# ============================================================================


@pytest.mark.parametrize(
    "title",
    [
        # Geopolitical triggers (US/Iran/China/etc + action verb)
        "Iran and US near memorandum of understanding as Bitcoin rallies past 82K",
        "Iran and US move closer to finalizing MOU as bitcoin surges past 82k",
        "Bitcoin surges above 82000 amid US-Iran de-escalation signals",
        "Trump announces Iran peace agreement, bitcoin heads higher",
        # Regulatory body action
        "SEC approves Nasdaq to list Bitcoin index options on the exchange",
        "Senate committee advances market structure bill to full senate, crypto rallies",
        "Circle stock explodes as long-stalled CLARITY Act passes Senate vote",
        # ETF / index-product
        "Bitwise Hyperliquid ETF to start trading Friday as hype rallies",
        # Institutional named-actor + verb
        "BlackRock bets on Circle as 222 million arc raise ignites CRCL stock surge",
        "Hype jumps as Coinbase and Circle back Hyperliquids stablecoin model",
        # Protocol/Technical milestone
        "Near Protocol to automate growth with dynamic resharding upgrade in June, NEAR token surges 27",
        "Sui mainnet to introduce private transactions, token surges over 20",
        # On-chain flow with named institutional metric
        "Morgan Stanley's MSBT ends first trading month with 0 outflows amid Bitcoin ETF inflow streak",
    ],
)
def test_f1_substantive_trigger_overrides_reactive_block(title: str) -> None:
    """F1: Headlines with substantive triggers must pass reactive-narrative gate."""
    from app.alerts.eligibility import _has_substantive_trigger, _is_reactive_bullish

    assert _has_substantive_trigger(title), f"Expected substantive trigger in: {title!r}"
    assert _is_reactive_bullish(title) is False, (
        f"Whitelist must override reactive block for: {title!r}"
    )


@pytest.mark.parametrize(
    "title",
    [
        # Pure price reaction without any named trigger — must STILL be blocked.
        "XRP spikes 2.5 beating bitcoin and ether in breakout above 1.45",
        "BuildOn B explodes 55 in 24 hours, is 0.74 the next stop",
        "Bitcoin surges past 78000 triggers 30m in short liquidations",
        "The real reason zcash zec is pumping",
        "The truth behind the TON pump",
        "BTC rallies on no news",
        "Bitcoin drops below 60K",
    ],
)
def test_f1_pure_price_reaction_still_blocked(title: str) -> None:
    """F1: Reactive titles without substantive triggers stay blocked."""
    from app.alerts.eligibility import _has_substantive_trigger

    assert _has_substantive_trigger(title) is False, (
        f"Should NOT have substantive trigger: {title!r}"
    )


def test_f1_iran_mou_e2e_passes_reactive_gate() -> None:
    """F1 E2E: the canonical Iran-MOU + BTC-rally headline reaches eligible=True."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.80,
        impact_score=0.70,
        title="Iran and US near memorandum of understanding as Bitcoin rallies past 82K",
        directional_confidence=0.85,
        actionable=True,
        priority=10,
        source_name="cryptobriefing",
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True
    assert decision.directional_block_reason is None


def test_f1_xrp_pure_pump_still_blocked_e2e() -> None:
    """F1 E2E: a pure-pump headline without trigger still hits reactive_price_narrative."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["XRP/USDT"],
        sentiment_score=0.75,
        impact_score=0.65,
        title="XRP spikes 2.5 in surprise pump",
        directional_confidence=0.85,
        actionable=True,
        priority=9,
        source_name="coindesk",
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == "reactive_price_narrative"


def test_f1_has_substantive_trigger_unit() -> None:
    """Direct test of `_has_substantive_trigger` matcher."""
    from app.alerts.eligibility import _has_substantive_trigger

    # Positive cases
    assert _has_substantive_trigger("US and Iran sign de-escalation deal") is True
    assert _has_substantive_trigger("SEC approves spot ETF") is True
    assert _has_substantive_trigger("CLARITY Act passed by Senate") is True
    assert _has_substantive_trigger("Nasdaq launches Bitcoin Index options") is True
    assert _has_substantive_trigger("MicroStrategy buys 10000 BTC") is True
    assert _has_substantive_trigger("Mainnet launch for Sui privacy features") is True

    # Negative cases (no substantive trigger)
    assert _has_substantive_trigger("BTC rallies on no news") is False
    assert _has_substantive_trigger("XRP spikes 2.5") is False
    assert _has_substantive_trigger("Bitcoin drops 5%") is False
    assert _has_substantive_trigger("ETH surges to weekly high") is False


def test_f1_whitelist_does_not_break_neutral_or_bearish_paths() -> None:
    """F1: Whitelist override only matters when reactive pattern matched —
    neutral sentiment is unaffected, bearish blocked by D-142 first."""
    # Neutral with surge in title: bearish_disabled gate is for bearish only,
    # neutral skips directional path entirely.
    decision = evaluate_directional_eligibility(
        sentiment_label="neutral",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.0,
        impact_score=0.5,
        title="Bitcoin surges past 82K amid Iran MOU",
        actionable=True,
    )
    assert decision.is_directional is False
    assert decision.directional_eligible is None


# ---------------------------------------------------------------------------
# D-227 / 2026-05-29: recall-proxy symbol resolution + operator-tunable
# bullish directional-confidence gate.
# ---------------------------------------------------------------------------


def test_resolve_eligible_symbols_keeps_supported_drops_naked_and_unmapped() -> None:
    """resolve_eligible_symbols mirrors the eligible branch: trading symbols
    survive, naked assets (no ``/``) and unresolvable tickers are dropped."""
    from app.alerts.eligibility import resolve_eligible_symbols

    result = resolve_eligible_symbols(["BTC/USDT", "ETH/USDT", "BTC", "NOTACOIN/USDT"])
    assert "BTC/USDT" in result
    assert "ETH/USDT" in result
    assert "BTC" not in result  # naked asset has no resolvable market
    # de-duplicates and preserves only resolvable symbols
    assert len(result) == len(set(result))


def test_resolve_eligible_symbols_empty_for_no_assets() -> None:
    from app.alerts.eligibility import resolve_eligible_symbols

    assert resolve_eligible_symbols([]) == []
    assert resolve_eligible_symbols(["", "  "]) == []


def test_bullish_confidence_threshold_defaults_to_constant() -> None:
    """Without env override the gate uses the historical 0.8 floor — no
    behaviour change is the safe default (D-227)."""
    from app.alerts.eligibility import (
        MIN_DIRECTIONAL_CONFIDENCE_BULLISH,
        _bullish_confidence_threshold,
    )

    assert _bullish_confidence_threshold() == MIN_DIRECTIONAL_CONFIDENCE_BULLISH


def test_bullish_confidence_gate_blocks_below_default_threshold() -> None:
    """A 0.7-confidence bullish alert is blocked under the 0.8 default — the
    population the D-148 recall proxy must measure before any loosening."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.9,
        impact_score=0.9,
        directional_confidence=0.7,
        actionable=True,
        priority=10,
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == "low_directional_confidence"


def test_bullish_confidence_gate_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lowering ALERT_MIN_DIRECTIONAL_CONFIDENCE_BULLISH admits the 0.7 alert
    without a redeploy; bearish stays hard-pinned regardless."""
    import app.alerts.eligibility as elig

    monkeypatch.setattr(elig, "_bullish_confidence_threshold", lambda: 0.7)
    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.9,
        impact_score=0.9,
        directional_confidence=0.7,
        actionable=True,
        priority=10,
    )
    assert decision.directional_eligible is True
    assert decision.directional_block_reason is None


# --- AUDIT-hotfix: event-loop wedge via per-alert get_settings() re-parse ---


def test_no_settings_reparse_when_confidence_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bullish threshold (which calls the UNCACHED get_settings() → .env
    re-parse) must NOT be resolved when no directional_confidence is supplied —
    that per-alert re-parse wedged the event loop under the dashboard poll."""
    import app.alerts.eligibility as elig

    calls = {"n": 0}

    def _spy() -> float:
        calls["n"] += 1
        return 0.8

    monkeypatch.setattr(elig, "_bullish_confidence_threshold", _spy)
    # hold-metrics style call: bullish, supported asset, NO directional_confidence
    elig.evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.9,
        impact_score=0.9,
        directional_confidence=None,
    )
    assert calls["n"] == 0  # threshold never resolved → no .env re-parse


def test_confidence_gate_still_enforced_when_supplied(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.alerts.eligibility as elig

    monkeypatch.setattr(elig, "_bullish_confidence_threshold", lambda: 0.8)
    decision = elig.evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.9,
        impact_score=0.9,
        directional_confidence=0.5,  # below 0.8 → blocked
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == elig.BLOCK_REASON_LOW_DIRECTIONAL_CONFIDENCE


def test_min_bullish_confidence_param_overrides_lazy_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loop caller can pass the pre-resolved threshold; the lazy resolver must
    then NOT be called (avoids per-iteration get_settings())."""
    import app.alerts.eligibility as elig

    calls = {"n": 0}
    monkeypatch.setattr(
        elig, "_bullish_confidence_threshold", lambda: calls.__setitem__("n", calls["n"] + 1)
    )
    decision = elig.evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.9,
        impact_score=0.9,
        directional_confidence=0.6,
        min_bullish_confidence=0.9,  # 0.6 < 0.9 → blocked, lazy resolver untouched
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == elig.BLOCK_REASON_LOW_DIRECTIONAL_CONFIDENCE
    assert calls["n"] == 0
