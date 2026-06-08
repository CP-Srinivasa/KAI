"""Cross-exchange per-venue plumbing (Issue #169).

Covers the three deliverables wired in this sprint:
- venue trust SSOT (known + fail-closed unknown)
- MarketDataPoint -> ProviderQuote mapping (incl. honest exclusion)
- aggregation hook + default-OFF flag invariant (no execution influence)
"""

from __future__ import annotations

from app.core.settings import AppSettings
from app.market_data.cross_exchange_aggregator import (
    aggregate_and_validate,
    cross_exchange_validation_enabled,
    run_cross_exchange_validation,
)
from app.market_data.models import MarketDataPoint
from app.market_data.quote_builder import Microstructure, build_provider_quote
from app.market_data.venue_trust import (
    UNKNOWN_VENUE_TRUST,
    venue_trust_score,
)

_NOW_MS = 1_000_000_000_000.0


def _point(source: str, price: float, *, ts: str = "", freshness: float = 0.0) -> MarketDataPoint:
    return MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc=ts,
        price=price,
        volume_24h=10_000_000.0,
        change_pct_24h=0.0,
        source=source,
        freshness_seconds=freshness,
    )


def _micro(bid: float, ask: float, depth: float = 300_000.0) -> Microstructure:
    return Microstructure(bid=bid, ask=ask, orderbook_depth=depth, latency_ms=20.0)


# --- venue trust SSOT -------------------------------------------------------


def test_venue_trust_known_venues() -> None:
    assert venue_trust_score("binance_futures") == 0.95
    assert venue_trust_score("BYBIT") == 0.90  # case-insensitive
    assert 0.0 <= venue_trust_score("coingecko") <= 1.0


def test_venue_trust_unknown_is_fail_closed_low() -> None:
    assert venue_trust_score("some_random_dex") == UNKNOWN_VENUE_TRUST
    assert venue_trust_score("") == UNKNOWN_VENUE_TRUST
    assert venue_trust_score(None) == UNKNOWN_VENUE_TRUST
    # an unknown venue is trusted LESS than every named one
    assert UNKNOWN_VENUE_TRUST < 0.7


# --- quote builder ----------------------------------------------------------


def test_build_quote_from_point_and_microstructure() -> None:
    q = build_provider_quote(_point("bybit", 60000.0), _micro(59990.0, 60010.0), now_ms=_NOW_MS)
    assert q is not None
    assert q.provider_id == "bybit"
    assert q.price == 60000.0
    assert q.bid == 59990.0
    assert q.ask == 60010.0
    assert q.exchange_trust_score == 0.90
    assert q.latency_ms == 20.0


def test_build_quote_excluded_without_microstructure() -> None:
    # no microstructure → excluded (None), NOT a faked zero-spread quote
    assert build_provider_quote(_point("bybit", 60000.0), None, now_ms=_NOW_MS) is None


def test_build_quote_excluded_on_bad_bid_ask() -> None:
    assert (
        build_provider_quote(_point("okx", 60000.0), _micro(60010.0, 59990.0), now_ms=_NOW_MS)
        is None
    )
    assert (
        build_provider_quote(_point("okx", 60000.0), _micro(0.0, 60010.0), now_ms=_NOW_MS) is None
    )


def test_build_quote_excluded_on_nonpositive_price() -> None:
    assert build_provider_quote(_point("okx", 0.0), _micro(1.0, 2.0), now_ms=_NOW_MS) is None


def test_quote_timestamp_from_freshness_when_iso_absent() -> None:
    q = build_provider_quote(
        _point("bybit", 60000.0, freshness=5.0), _micro(59990.0, 60010.0), now_ms=_NOW_MS
    )
    assert q is not None
    # 5s stale → 5000 ms before now
    assert q.timestamp_ms == _NOW_MS - 5000.0


# --- aggregation hook -------------------------------------------------------


def test_aggregate_runs_median_over_microstructure_quotes() -> None:
    inputs = [
        (_point("bybit", 60000.0), _micro(59990.0, 60010.0)),
        (_point("okx", 60020.0), _micro(60010.0, 60030.0)),
        (_point("binance_futures", 59980.0), _micro(59970.0, 59990.0)),
    ]
    agg = aggregate_and_validate("BTC/USDT", inputs, now_ms=_NOW_MS)
    assert agg.enabled is True
    assert agg.providers_in == 3
    assert agg.quotes_built == 3
    assert agg.excluded_count == 0
    assert agg.validation is not None
    assert agg.validation.validated_price is not None
    assert agg.influences_execution is False


def test_aggregate_excludes_venues_without_microstructure() -> None:
    inputs = [
        (_point("bybit", 60000.0), _micro(59990.0, 60010.0)),
        (_point("coingecko", 60050.0), None),  # aggregator has no book → excluded
    ]
    agg = aggregate_and_validate("BTC/USDT", inputs, now_ms=_NOW_MS)
    assert agg.providers_in == 2
    assert agg.quotes_built == 1
    assert agg.excluded_count == 1
    assert "coingecko" in agg.excluded_providers


def test_aggregate_no_microstructure_at_all_yields_reason() -> None:
    inputs = [(_point("coingecko", 60050.0), None)]
    agg = aggregate_and_validate("BTC/USDT", inputs, now_ms=_NOW_MS)
    assert agg.quotes_built == 0
    assert agg.validation is None
    assert agg.reason == "no_microstructure_quotes"


# --- default-OFF flag invariant --------------------------------------------


def test_flag_default_off() -> None:
    s = AppSettings()
    assert cross_exchange_validation_enabled(s) is False


def test_run_disabled_does_not_validate(monkeypatch) -> None:
    s = AppSettings()
    inputs = [(_point("bybit", 60000.0), _micro(59990.0, 60010.0))]
    agg = run_cross_exchange_validation("BTC/USDT", inputs, s, now_ms=_NOW_MS)
    assert agg.enabled is False
    assert agg.validation is None
    assert agg.reason == "cross_exchange_validation_disabled"
    assert agg.influences_execution is False


def test_run_enabled_validates(monkeypatch) -> None:
    monkeypatch.setenv("APP_CROSS_EXCHANGE_VALIDATION_ENABLED", "true")
    s = AppSettings()
    assert cross_exchange_validation_enabled(s) is True
    inputs = [
        (_point("bybit", 60000.0), _micro(59990.0, 60010.0)),
        (_point("okx", 60010.0), _micro(60000.0, 60020.0)),
    ]
    agg = run_cross_exchange_validation("BTC/USDT", inputs, s, now_ms=_NOW_MS)
    assert agg.enabled is True
    assert agg.validation is not None
    assert agg.influences_execution is False
