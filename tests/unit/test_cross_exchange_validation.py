"""Cross-exchange weighted-median validation (market-data gate delta).

Guards the Satoshi contract: a single venue tick — flash spike, stale feed,
delisted-instrument phantom, manipulation — must not drive KAI's validated
price, stops or risk read. The existing binary disagreement check in
``FallbackMarketDataAdapter`` is unrelated and stays covered by
``test_fallback_provider_disagreement.py``.
"""

from __future__ import annotations

import math

from app.market_data.cross_exchange import (
    DATA_QUALITY_VERSION,
    CrossExchangeConfig,
    ProviderQuote,
    dynamic_desync_threshold,
    freshness_score,
    spread_bps,
    spread_score,
    validate_cross_exchange,
    weighted_median,
)
from app.market_data.regime_detection import MarketRegime

NOW_MS = 1_700_000_000_000.0


def _q(
    provider_id: str,
    price: float,
    *,
    age_ms: float = 0.0,
    spread_pct: float = 0.0002,  # 2 bps default — tight, liquid
    volume: float = 12_000_000.0,
    depth: float = 600_000.0,
    trust: float = 0.9,
    latency_ms: float = 50.0,
) -> ProviderQuote:
    half = price * spread_pct / 2.0
    return ProviderQuote(
        provider_id=provider_id,
        price=price,
        bid=price - half,
        ask=price + half,
        volume=volume,
        orderbook_depth=depth,
        timestamp_ms=NOW_MS - age_ms,
        exchange_trust_score=trust,
        latency_ms=latency_ms,
    )


def _validate(quotes: list[ProviderQuote], **kw: object) -> object:
    return validate_cross_exchange(
        "BTC/USDT",
        quotes,
        now_ms=NOW_MS,
        **kw,  # type: ignore[arg-type]
    )


# ─── Mandated scenarios ───────────────────────────────────────────────────────


def test_binance_flash_spike_does_not_move_validated_price() -> None:
    quotes = [
        _q("binance", 130_000.0),  # flash spike +30%
        _q("coinbase", 100_000.0),
        _q("kraken", 100_050.0),
    ]
    res = _validate(quotes)

    assert res.is_execution_safe
    assert res.reject_reason is None
    # validated price tracks the two stable venues, not the spike
    assert 99_000.0 <= res.validated_price <= 101_000.0
    assert "binance" in res.provider_desyncs
    assert "coinbase" not in res.provider_desyncs
    assert res.weighted_median_confidence > 0.5


def test_single_stale_provider_is_excluded_not_rejected() -> None:
    quotes = [
        _q("coinbase", 100_000.0),
        _q("kraken", 100_050.0),
        _q("okx", 100_020.0, age_ms=180_000.0),  # 3 min old → stale
    ]
    res = _validate(quotes)

    assert res.reject_reason is None
    assert res.is_execution_safe
    assert 99_900.0 <= res.validated_price <= 100_150.0
    stale = {a.provider_id: a for a in res.assessments}["okx"]
    assert stale.excluded_reason == "stale"
    assert stale.weight == 0.0
    assert res.freshness_ms_max >= 180_000.0


def test_two_strongly_contradicting_providers_are_rejected() -> None:
    quotes = [
        _q("venue_a", 100_000.0),
        _q("venue_b", 130_000.0),  # 30% apart, both fresh & liquid
    ]
    res = _validate(quotes)

    assert not res.is_execution_safe
    assert res.reject_reason in {
        "no_weighted_consensus",
        "providers_disagree_no_consensus",
    }


def test_all_providers_stale_rejects_with_no_price() -> None:
    quotes = [
        _q("coinbase", 100_000.0, age_ms=200_000.0),
        _q("kraken", 100_050.0, age_ms=200_000.0),
        _q("okx", 99_980.0, age_ms=200_000.0),
    ]
    res = _validate(quotes)

    assert res.validated_price is None
    assert res.reject_reason == "all_providers_stale"
    assert not res.is_execution_safe


def test_illiquid_wide_spread_coin_is_priced_not_rejected() -> None:
    # Thin alt: 8% spread, tiny volume/depth, but venues agree on the mid.
    quotes = [
        _q("dex_a", 1.000, spread_pct=0.08, volume=40_000.0, depth=3_000.0, trust=0.6),
        _q("dex_b", 1.004, spread_pct=0.07, volume=35_000.0, depth=2_500.0, trust=0.6),
        _q("cex_c", 0.998, spread_pct=0.06, volume=60_000.0, depth=5_000.0, trust=0.7),
    ]
    res = _validate(quotes)

    assert res.reject_reason is None  # wide spread alone must not reject
    assert res.validated_price is not None
    assert 0.99 <= res.validated_price <= 1.01
    assert res.spread_bps_median > 300.0  # genuinely wide
    assert res.liquidity_score < 0.8  # flagged as thinner than a major


def test_consistent_jump_in_panic_regime_not_falsely_rejected() -> None:
    # Sharp but *coherent* −10% move; one venue 2.5% off the pack.
    quotes = [
        _q("coinbase", 90_000.0),
        _q("kraken", 90_300.0),
        _q("binance", 92_250.0),  # 2.5% above the cluster
    ]
    panic = _validate(quotes, volatility=0.05, regime=MarketRegime.PANIC)
    calm = _validate(quotes, volatility=0.0, regime=MarketRegime.BULL)

    # Wide panic band tolerates the dispersion → nothing desynced.
    assert panic.reject_reason is None
    assert panic.provider_desyncs == []
    assert panic.is_execution_safe
    # Same data in a calm, tight-band regime flags the 2.5% outlier.
    assert "binance" in calm.provider_desyncs


# ─── Output schema contract ───────────────────────────────────────────────────


def test_output_dict_matches_canonical_schema() -> None:
    res = _validate([_q("a", 100.0), _q("b", 100.05), _q("c", 99.97)])
    out = res.to_output_dict()
    assert set(out.keys()) == {
        "asset_id",
        "validated_price",
        "raw_provider_prices",
        "weighted_median_confidence",
        "provider_desyncs",
        "freshness_ms_max",
        "spread_bps_median",
        "liquidity_score",
        "reject_reason",
        "data_quality_version",
    }
    assert out["data_quality_version"] == DATA_QUALITY_VERSION
    assert out["raw_provider_prices"] == {"a": 100.0, "b": 100.05, "c": 99.97}


def test_single_provider_cannot_be_cross_validated() -> None:
    res = _validate([_q("only", 100.0)])
    assert res.reject_reason == "insufficient_cross_validation"
    assert not res.is_execution_safe


def test_empty_quotes_rejected() -> None:
    res = _validate([])
    assert res.reject_reason == "no_providers"
    assert res.validated_price is None


# ─── Scoring primitives ───────────────────────────────────────────────────────


def test_weighted_median_midpoint_on_equal_weights() -> None:
    assert weighted_median([(100.0, 1.0), (102.0, 1.0)]) == 101.0


def test_weighted_median_follows_weight_mass() -> None:
    # Heavy weight on 100 pulls the median there despite an outlier at 200.
    assert weighted_median([(100.0, 9.0), (200.0, 1.0)]) == 100.0


def test_freshness_score_ramp() -> None:
    cfg = CrossExchangeConfig()
    assert freshness_score(0.0, 0.0, cfg) == 1.0
    assert freshness_score(cfg.max_staleness_ms, 0.0, cfg) == 0.0
    assert freshness_score(cfg.max_staleness_ms * 2, 0.0, cfg) == 0.0
    mid = freshness_score(31_000.0, 0.0, cfg)
    assert 0.0 < mid < 1.0
    # Latency counts as staleness.
    assert freshness_score(1_000.0, 70_000.0, cfg) == 0.0


def test_spread_scoring() -> None:
    cfg = CrossExchangeConfig()
    assert spread_bps(99.99, 100.01) > 0
    assert math.isinf(spread_bps(101.0, 100.0))  # crossed book
    assert spread_score(2.0, cfg) == 1.0
    assert spread_score(1_000.0, cfg) == cfg.spread_score_floor


def test_dynamic_threshold_widens_in_panic_and_caps() -> None:
    cfg = CrossExchangeConfig()
    calm = dynamic_desync_threshold(0.0, MarketRegime.BULL, cfg)
    panic = dynamic_desync_threshold(0.02, MarketRegime.PANIC, cfg)
    manip = dynamic_desync_threshold(0.0, MarketRegime.HIGH_MANIPULATION, cfg)
    assert panic > calm
    assert manip < calm  # manipulation tightens, not widens
    assert panic <= cfg.max_desync_threshold_pct  # hard cap honoured
