"""Unit tests for the Manipulation Detection Engine."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.risk.manipulation_detection import (
    ManipulationDetectionEngine,
    _bigrams,
    _combine_independent,
    _jaccard,
    _normalized_entropy,
    _pearson_correlation,
    _union_find_clusters,
    _zscore,
)
from app.risk.manipulation_detection_models import (
    ALL_PATTERNS,
    PATTERN_ABNORMAL_WALLET,
    PATTERN_BOT_NETWORK,
    PATTERN_COORDINATED_SHILLING,
    PATTERN_FAKE_ENGAGEMENT,
    PATTERN_SPOOFING,
    PATTERN_WASH_TRADING,
    SOURCE_MARKET_ACCOUNT,
    SOURCE_SOCIAL_ACCOUNT,
    SOURCE_WALLET,
    Account,
    HistoricalCall,
    ManipulationDetectionConfig,
    OrderEvent,
    Post,
    PriceBar,
    Trade,
    WalletTx,
)

# --------------------------------------------------------------------- helpers

BASE_TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _ts(seconds_offset: float) -> str:
    return (BASE_TS + timedelta(seconds=seconds_offset)).isoformat()


def _make_post(
    *,
    source: str,
    text: str,
    sec_offset: float = 0.0,
    engagement: int = 10,
    followers: int = 1000,
    sentiment: float = 0.5,
    asset: str = "BTC",
) -> Post:
    return Post(
        post_id=f"p_{source}_{int(sec_offset)}",
        source_id=source,
        timestamp_utc=_ts(sec_offset),
        text=text,
        asset_mentions=(asset,),
        sentiment_score=sentiment,
        engagement_count=engagement,
        follower_count_at_post=followers,
    )


# ============================================================================
# Pure helpers
# ============================================================================


def test_bigrams_lowercases_and_alphanumeric_only():
    bg = _bigrams("Hello, World!")
    assert "he" in bg and "wo" in bg
    # No bigram itself should contain non-alphanumerics
    assert all(b.isalnum() for b in bg)


def test_jaccard_matches_textbook_values():
    assert _jaccard(set(), set()) == 0.0
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert _jaccard({"a"}, {"b"}) == 0.0
    assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3, abs=1e-9)


def test_zscore_returns_zero_on_constant_sample():
    assert _zscore(5.0, [5.0, 5.0, 5.0]) == 0.0


def test_zscore_picks_up_outliers():
    sample = [1.0, 1.1, 0.9, 1.0]
    assert _zscore(10.0, sample) > 5.0


def test_normalized_entropy_machine_like_low():
    """Perfectly regular intervals → low entropy."""
    intervals = [60.0] * 50
    assert _normalized_entropy(intervals) < 0.2


def test_normalized_entropy_human_like_high():
    """Random intervals → entropy near 1."""
    rng = random.Random(7)
    intervals = [rng.uniform(10, 1000) for _ in range(200)]
    assert _normalized_entropy(intervals) > 0.5


def test_union_find_groups_connected_edges():
    edges = [(0, 1), (1, 2), (3, 4)]
    clusters = _union_find_clusters(edges, 5)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [2, 3]


def test_combine_independent_caps_at_one_for_strong_signal():
    assert _combine_independent([1.0, 0.5]) == pytest.approx(1.0, abs=1e-9)
    assert _combine_independent([0.5, 0.5]) == pytest.approx(0.75, abs=1e-9)
    assert _combine_independent([]) == 0.0


def test_pearson_correlation_perfect_positive():
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [2.0, 4.0, 6.0, 8.0]
    assert _pearson_correlation(xs, ys) == pytest.approx(1.0, abs=1e-9)


# ============================================================================
# Coordinated shilling
# ============================================================================


def test_coordinated_shilling_detects_clustered_identical_posts():
    eng = ManipulationDetectionEngine()
    text = "BTC moon soon — buy now, this is your chance!!!"
    posts = [_make_post(source=f"acc_{i}", text=text, sec_offset=i * 30) for i in range(5)]
    out = eng.analyze(posts=posts)
    assert out.coordinated_shilling_events >= 1
    suspect_sources = {
        s.source_id for s in out.sources if PATTERN_COORDINATED_SHILLING in s.detected_patterns
    }
    assert len(suspect_sources) >= 3


def test_coordinated_shilling_ignores_natural_diverse_posts():
    eng = ManipulationDetectionEngine()
    posts = [
        _make_post(source="alice", text="Hi everyone, BTC view bullish today.", sec_offset=0),
        _make_post(source="bob", text="I bought ETH last week at 2400, good.", sec_offset=60),
        _make_post(source="carol", text="Thoughts on the upcoming Fed meeting?", sec_offset=120),
        _make_post(source="dan", text="Solana ecosystem keeps shipping tools.", sec_offset=180),
    ]
    out = eng.analyze(posts=posts)
    assert out.coordinated_shilling_events == 0


def test_coordinated_shilling_window_excludes_far_apart_posts():
    eng = ManipulationDetectionEngine()
    text = "BTC to the moon — buy now!"
    # Three identical posts but spaced one hour apart → outside 5 min window
    posts = [_make_post(source=f"acc_{i}", text=text, sec_offset=i * 3600) for i in range(3)]
    out = eng.analyze(posts=posts)
    assert out.coordinated_shilling_events == 0


# ============================================================================
# Fake engagement
# ============================================================================


def test_fake_engagement_flags_extreme_outliers():
    eng = ManipulationDetectionEngine()
    posts = [
        _make_post(source="normal", text=f"post {i}", engagement=10, followers=1000, sec_offset=i)
        for i in range(20)
    ]
    posts.append(
        _make_post(
            source="suspect",
            text="hot take",
            engagement=50_000,  # 50× the baseline
            followers=100,  # tiny audience
            sec_offset=21,
        )
    )
    out = eng.analyze(posts=posts)
    assert out.fake_engagement_events >= 1
    suspect = next(s for s in out.sources if s.source_id == "suspect")
    assert PATTERN_FAKE_ENGAGEMENT in suspect.detected_patterns


def test_fake_engagement_zero_followers_with_high_engagement_is_suspect():
    eng = ManipulationDetectionEngine()
    posts = [
        _make_post(source="bot", text="hype", engagement=500, followers=0, sec_offset=i)
        for i in range(10)
    ]
    out = eng.analyze(posts=posts)
    assert out.fake_engagement_events >= 1


# ============================================================================
# Bot network
# ============================================================================


def test_bot_network_flags_young_regular_accounts():
    eng = ManipulationDetectionEngine()
    # 5 fresh accounts, posting every 60 seconds (low entropy intervals)
    accounts = [
        Account(
            account_id=f"bot_{i}",
            account_age_days=5,
            follower_count=2,
            following_count=200,
            has_default_avatar=True,
            bio_length=0,
        )
        for i in range(5)
    ]
    posts: list[Post] = []
    for i in range(5):
        for j in range(15):
            posts.append(
                _make_post(
                    source=f"bot_{i}",
                    text=f"shill #{j}",
                    sec_offset=j * 60,
                )
            )
    out = eng.analyze(accounts=accounts, posts=posts)
    assert out.bot_networks_detected >= 1
    flagged = [s for s in out.sources if PATTERN_BOT_NETWORK in s.detected_patterns]
    assert len(flagged) >= 3


def test_bot_network_does_not_flag_aged_diverse_accounts():
    eng = ManipulationDetectionEngine()
    accounts = [
        Account(
            account_id=f"human_{i}",
            account_age_days=2000,
            follower_count=5000,
            following_count=300,
            has_default_avatar=False,
            bio_length=120,
            verified=True,
        )
        for i in range(3)
    ]
    out = eng.analyze(accounts=accounts)
    assert out.bot_networks_detected == 0


# ============================================================================
# Wash trading
# ============================================================================


def test_wash_trading_signature_high_for_self_pair_dominated_book():
    eng = ManipulationDetectionEngine()
    # 60 trades all with the same buyer == seller
    trades = [
        Trade(
            trade_id=f"t_{i}",
            symbol="BTC/USDT",
            timestamp_utc=_ts(i),
            price=50_000.0 + (i % 3),  # tiny price moves
            size=1.0,
            side="buy" if i % 2 == 0 else "sell",
            buyer_id="washer",
            seller_id="washer",
        )
        for i in range(60)
    ]
    out = eng.analyze(trades=trades)
    assert out.wash_trading_signature is not None
    assert out.wash_trading_signature > 0.5
    suspect = next(s for s in out.sources if s.source_id == "washer")
    assert PATTERN_WASH_TRADING in suspect.detected_patterns


def test_wash_trading_none_for_too_few_trades():
    eng = ManipulationDetectionEngine()
    trades = [
        Trade(
            trade_id=f"t_{i}",
            symbol="BTC/USDT",
            timestamp_utc=_ts(i),
            price=50_000.0,
            size=1.0,
            side="buy",
        )
        for i in range(5)
    ]
    out = eng.analyze(trades=trades)
    assert out.wash_trading_signature is None


# ============================================================================
# Spoofing
# ============================================================================


def test_spoofing_flags_high_cancel_ratio_outsized_orders():
    eng = ManipulationDetectionEngine()
    events: list[OrderEvent] = []
    # 40 huge orders by "spoofer", 38 canceled, 2 filled
    for i in range(40):
        events.append(
            OrderEvent(
                event_id=f"se_{i}_p",
                symbol="BTC/USDT",
                timestamp_utc=_ts(i),
                account_id="spoofer",
                side="buy",
                price=50_000.0,
                size=100.0,  # 10× the baseline
                event_type="placed",
            )
        )
        events.append(
            OrderEvent(
                event_id=f"se_{i}_x",
                symbol="BTC/USDT",
                timestamp_utc=_ts(i + 0.5),
                account_id="spoofer",
                side="buy",
                price=50_000.0,
                size=100.0,
                event_type=("filled" if i < 2 else "canceled"),
            )
        )
    # Background normal accounts with small fills
    for i in range(40):
        events.append(
            OrderEvent(
                event_id=f"ne_{i}",
                symbol="BTC/USDT",
                timestamp_utc=_ts(i),
                account_id=f"trader_{i % 4}",
                side="buy",
                price=50_000.0,
                size=10.0,
                event_type="filled",
            )
        )
    out = eng.analyze(order_events=events)
    assert out.spoofing_signature is not None
    assert out.spoofing_signature > 0.5
    suspect = next(s for s in out.sources if s.source_id == "spoofer")
    assert PATTERN_SPOOFING in suspect.detected_patterns


# ============================================================================
# Pump and dump
# ============================================================================


def test_pump_and_dump_signature_for_sequence_match():
    cfg = ManipulationDetectionConfig(
        pump_window_bars=10,
        pump_price_increase_threshold=0.30,
        dump_window_bars=5,
        dump_price_decrease_threshold=0.20,
        pump_volume_zscore_threshold=2.0,
    )
    eng = ManipulationDetectionEngine(cfg)
    bars: list[PriceBar] = []
    # 100 baseline bars at ~$50 with low volume
    rng = random.Random(11)
    price = 50.0
    for i in range(100):
        c = price * (1.0 + rng.gauss(0.0, 0.005))
        bars.append(
            PriceBar(
                symbol="BTC/USDT",
                timestamp_utc=_ts(i * 60),
                open=price,
                high=max(price, c),
                low=min(price, c),
                close=c,
                volume=rng.uniform(1.0, 5.0),
            )
        )
        price = c

    # Pump: 10 bars up to +50%, on 5× volume
    for i in range(10):
        c = price * 1.04
        bars.append(
            PriceBar(
                symbol="BTC/USDT",
                timestamp_utc=_ts((100 + i) * 60),
                open=price,
                high=c,
                low=price,
                close=c,
                volume=rng.uniform(15.0, 25.0),
            )
        )
        price = c
    # Dump: 5 bars −25%
    for i in range(5):
        c = price * 0.94
        bars.append(
            PriceBar(
                symbol="BTC/USDT",
                timestamp_utc=_ts((110 + i) * 60),
                open=price,
                high=price,
                low=c,
                close=c,
                volume=rng.uniform(20.0, 40.0),
            )
        )
        price = c

    out = eng.analyze(bars=bars, target_symbol="BTC")
    assert out.pump_and_dump_signature is not None
    assert out.pump_and_dump_signature > 0.3


def test_pump_and_dump_none_for_short_history():
    eng = ManipulationDetectionEngine()
    bars = [
        PriceBar(
            symbol="BTC/USDT",
            timestamp_utc=_ts(i * 60),
            open=50.0,
            high=50.0,
            low=50.0,
            close=50.0,
            volume=1.0,
        )
        for i in range(5)
    ]
    out = eng.analyze(bars=bars)
    assert out.pump_and_dump_signature is None


# ============================================================================
# Abnormal wallet
# ============================================================================


def test_abnormal_wallet_detects_volume_zscore_spike():
    eng = ManipulationDetectionEngine()
    rng = random.Random(2)
    txs: list[WalletTx] = []
    # 20 small transactions
    for i in range(20):
        txs.append(
            WalletTx(
                tx_id=f"tx_{i}",
                timestamp_utc=_ts(i * 86400),
                from_wallet="whale",
                to_wallet=f"counterparty_{i}",
                asset="BTC",
                amount=1.0,
                usd_value=rng.uniform(100, 500),
            )
        )
    # 1 huge transaction last
    txs.append(
        WalletTx(
            tx_id="tx_big",
            timestamp_utc=_ts(21 * 86400),
            from_wallet="whale",
            to_wallet="exchange",
            asset="BTC",
            amount=1000.0,
            usd_value=50_000_000,
        )
    )
    out = eng.analyze(wallet_txs=txs)
    assert out.abnormal_wallet_flows >= 1
    whale = next(s for s in out.sources if s.source_id == "whale")
    assert PATTERN_ABNORMAL_WALLET in whale.detected_patterns


def test_abnormal_wallet_funnel_pattern():
    eng = ManipulationDetectionEngine(ManipulationDetectionConfig(wallet_funnel_min_sources=4))
    txs = [
        WalletTx(
            tx_id=f"funnel_{i}",
            timestamp_utc=_ts(i),
            from_wallet=f"src_{i}",
            to_wallet="aggregator",
            asset="BTC",
            amount=1.0,
            usd_value=10_000.0,
        )
        for i in range(6)
    ]
    out = eng.analyze(wallet_txs=txs)
    aggr = next(s for s in out.sources if s.source_id == "aggregator")
    assert PATTERN_ABNORMAL_WALLET in aggr.detected_patterns


# ============================================================================
# Insider behavior
# ============================================================================


def test_insider_behavior_flags_lead_lag_correlation():
    cfg = ManipulationDetectionConfig(
        insider_lead_window_bars=5,
        insider_correlation_threshold=0.25,
        insider_min_observations=20,
    )
    eng = ManipulationDetectionEngine(cfg)
    # Synthetic: insider buys 5 bars BEFORE every up-move
    rng = random.Random(7)
    n_bars = 100
    bars: list[PriceBar] = []
    price = 50.0
    for i in range(n_bars):
        if 30 <= i <= 70 and i % 10 == 0:
            c = price * 1.05  # scheduled up-moves
        else:
            c = price * (1.0 + rng.gauss(0.0, 0.005))
        bars.append(
            PriceBar(
                symbol="BTC/USDT",
                timestamp_utc=_ts(i * 60),
                open=price,
                high=max(price, c),
                low=min(price, c),
                close=c,
                volume=1.0,
            )
        )
        price = c

    # Insider wallet buys 5 bars before each scheduled up-move
    txs: list[WalletTx] = []
    for i in [25, 35, 45, 55, 65]:
        txs.append(
            WalletTx(
                tx_id=f"ins_{i}",
                timestamp_utc=_ts(i * 60),
                from_wallet="exchange",
                to_wallet="insider",
                asset="BTC",
                amount=10.0,
                usd_value=500_000.0,
            )
        )
    out = eng.analyze(wallet_txs=txs, bars=bars, target_symbol="BTC")
    assert out.insider_behavior_signature is not None
    assert out.insider_behavior_signature > 0.0


# ============================================================================
# Source-level aggregation + historical reliability
# ============================================================================


def test_historical_reliability_smoothes_with_few_samples():
    eng = ManipulationDetectionEngine()
    calls = [
        HistoricalCall(
            source_id="alice",
            timestamp_utc=_ts(i),
            asset="BTC",
            direction="bullish",
            realized_pnl_pct_30d=10.0,
        )
        for i in range(2)
    ]
    out = eng.analyze(historical_calls=calls)
    alice = next(s for s in out.sources if s.source_id == "alice")
    # Less than min_samples → reliability stays close to neutral
    assert abs(alice.historical_reliability - 0.5) < 0.05


def test_historical_reliability_rewards_track_record():
    eng = ManipulationDetectionEngine()
    calls = [
        HistoricalCall(
            source_id="oracle",
            timestamp_utc=_ts(i),
            asset="BTC",
            direction="bullish",
            realized_pnl_pct_30d=15.0,
        )
        for i in range(20)
    ]
    out = eng.analyze(historical_calls=calls)
    oracle = next(s for s in out.sources if s.source_id == "oracle")
    assert oracle.historical_reliability > 0.7


def test_trust_score_collapses_under_strong_manipulation_evidence():
    eng = ManipulationDetectionEngine()
    text = "ALT MOON 1000X — buy now, this is your chance!!!"
    # 5 accounts with identical posts → coordinated shilling cluster
    posts = [_make_post(source=f"shill_{i}", text=text, sec_offset=i * 10) for i in range(5)]
    out = eng.analyze(posts=posts)
    flagged = [s for s in out.sources if PATTERN_COORDINATED_SHILLING in s.detected_patterns]
    assert flagged
    for s in flagged:
        assert s.trust_score < 0.5
        assert s.manipulation_probability > 0.0


def test_clean_source_has_high_trust_score_with_history():
    eng = ManipulationDetectionEngine()
    posts = [
        _make_post(
            source="alice",
            text=f"Genuine market view #{i}",
            sec_offset=i * 3600,
            engagement=20,
        )
        for i in range(10)
    ]
    calls = [
        HistoricalCall(
            source_id="alice",
            timestamp_utc=_ts(i),
            asset="BTC",
            direction="bullish",
            realized_pnl_pct_30d=8.0,
        )
        for i in range(15)
    ]
    out = eng.analyze(posts=posts, historical_calls=calls)
    alice = next(s for s in out.sources if s.source_id == "alice")
    assert alice.detected_patterns == []
    assert alice.trust_score > 0.6
    assert alice.manipulation_probability == 0.0


# ============================================================================
# Edge cases
# ============================================================================


def test_empty_inputs_produce_empty_report():
    eng = ManipulationDetectionEngine()
    out = eng.analyze()
    assert out.sources == []
    assert "no_input_data" in out.warnings
    # Signatures stay None when no data feeds them
    assert out.wash_trading_signature is None
    assert out.spoofing_signature is None
    assert out.pump_and_dump_signature is None


def test_to_json_dict_contains_all_required_sections():
    eng = ManipulationDetectionEngine()
    posts = [_make_post(source="x", text="hi there", sec_offset=0)]
    out = eng.analyze(posts=posts)
    payload = out.to_json_dict()
    for key in ("events", "signatures", "sources", "inputs_summary", "inputs_hash"):
        assert key in payload
    assert payload["report_type"] == "manipulation_detection"
    assert payload["inputs_summary"]["patterns_known"] == len(ALL_PATTERNS)


def test_source_type_set_correctly_per_detector():
    """Social posts → SOURCE_SOCIAL_ACCOUNT; trades → SOURCE_MARKET_ACCOUNT;
    wallet flows → SOURCE_WALLET."""
    eng = ManipulationDetectionEngine()
    posts = [_make_post(source="poster", text="hi", sec_offset=0)]
    trades = [
        Trade(
            trade_id=f"t_{i}",
            symbol="BTC/USDT",
            timestamp_utc=_ts(i),
            price=50_000.0,
            size=1.0,
            side="buy",
            buyer_id="trader",
            seller_id="trader",
        )
        for i in range(60)
    ]
    txs = [
        WalletTx(
            tx_id=f"x_{i}",
            timestamp_utc=_ts(i),
            from_wallet=f"src_{i}",
            to_wallet="hub",
            asset="BTC",
            amount=1.0,
            usd_value=1000.0,
        )
        for i in range(6)
    ]
    out = eng.analyze(posts=posts, trades=trades, wallet_txs=txs)
    types = {s.source_type for s in out.sources}
    assert SOURCE_SOCIAL_ACCOUNT in types
    assert SOURCE_MARKET_ACCOUNT in types
    assert SOURCE_WALLET in types
