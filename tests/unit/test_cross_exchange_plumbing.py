"""Cross-exchange per-venue plumbing (Issue #169).

Covers the deliverables wired in this sprint:
- venue trust SSOT (known + fail-closed unknown)
- MarketDataPoint -> ProviderQuote mapping (incl. honest exclusion)
"""

from __future__ import annotations

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
