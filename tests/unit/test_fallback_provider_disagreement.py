"""DS-20260529-V1: cross-provider sanity in FallbackMarketDataAdapter.

Regression guard for the 2026-05-28 MATIC phantom-PnL incident: BitMEX kept a
delisted "MATIC" instrument priced at 0.40875 after the POL rebrand while every
other venue priced ~0.088. Because the fallback chain returned whichever
provider resolved first and entry vs. monitor ticks hit different providers, the
paper book booked +73,548 USD of phantom profit. The fix tags a point stale
when two fresh providers disagree beyond the tolerance so entry/monitor skip it.
"""

from __future__ import annotations

import pytest

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.market_data.models import OHLCV, MarketDataPoint, Ticker
from app.market_data.service import _MOCK_SOURCE, FallbackMarketDataAdapter


class _FakeAdapter(BaseMarketDataAdapter):
    """Returns a preset MarketDataPoint (or None) for any symbol."""

    def __init__(self, name: str, point: MarketDataPoint | None) -> None:
        self._name = name
        self._point = point

    @property
    def adapter_name(self) -> str:
        return self._name

    async def get_ticker(self, symbol: str) -> Ticker | None:  # pragma: no cover
        return None

    async def get_ohlcv(  # pragma: no cover
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        return []

    async def get_price(self, symbol: str) -> float | None:  # pragma: no cover
        return self._point.price if self._point else None

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
        return self._point


def _pt(source: str, price: float, *, is_stale: bool = False) -> MarketDataPoint:
    return MarketDataPoint(
        symbol="MATIC/USDT",
        timestamp_utc="2026-05-29T10:00:00+00:00",
        price=price,
        volume_24h=1000.0,
        change_pct_24h=0.0,
        source=source,
        is_stale=is_stale,
    )


@pytest.mark.asyncio
async def test_agreeing_providers_return_first_not_stale() -> None:
    chain = FallbackMarketDataAdapter(
        [_FakeAdapter("bybit", _pt("bybit", 0.0878)), _FakeAdapter("cg", _pt("cg", 0.0879))]
    )
    point = await chain.get_market_data_point("MATIC/USDT")
    assert point is not None
    assert point.is_stale is False
    assert point.source == "bybit"


@pytest.mark.asyncio
async def test_disagreeing_providers_tagged_stale() -> None:
    # BitMEX legacy 0.40875 vs CoinGecko real 0.087817 — the actual incident.
    chain = FallbackMarketDataAdapter(
        [_FakeAdapter("bitmex", _pt("bitmex", 0.40875)), _FakeAdapter("cg", _pt("cg", 0.087817))]
    )
    point = await chain.get_market_data_point("MATIC/USDT")
    assert point is not None
    assert point.is_stale is True
    assert "provider_disagreement" in point.source


@pytest.mark.asyncio
async def test_none_providers_skipped_before_crosscheck() -> None:
    # Bybit/Binance/OKX return None (MATIC delisted), only BitMEX + CG resolve.
    chain = FallbackMarketDataAdapter(
        [
            _FakeAdapter("bybit", None),
            _FakeAdapter("binance", None),
            _FakeAdapter("okx", None),
            _FakeAdapter("bitmex", _pt("bitmex", 0.40875)),
            _FakeAdapter("cg", _pt("cg", 0.087817)),
        ]
    )
    point = await chain.get_market_data_point("MATIC/USDT")
    assert point is not None
    assert point.is_stale is True


@pytest.mark.asyncio
async def test_single_provider_not_crosschecked() -> None:
    chain = FallbackMarketDataAdapter(
        [_FakeAdapter("bybit", None), _FakeAdapter("only", _pt("only", 0.40875))]
    )
    point = await chain.get_market_data_point("MATIC/USDT")
    assert point is not None
    assert point.is_stale is False
    assert point.source == "only"


@pytest.mark.asyncio
async def test_all_none_returns_none() -> None:
    chain = FallbackMarketDataAdapter([_FakeAdapter("a", None), _FakeAdapter("b", None)])
    assert await chain.get_market_data_point("MATIC/USDT") is None


@pytest.mark.asyncio
async def test_threshold_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 20% spread is tolerated when the floor is raised to 50%.
    monkeypatch.setenv("MARKET_DATA_PROVIDER_DISAGREEMENT_PCT", "0.50")
    chain = FallbackMarketDataAdapter(
        [_FakeAdapter("a", _pt("a", 1.00)), _FakeAdapter("b", _pt("b", 1.20))]
    )
    point = await chain.get_market_data_point("MATIC/USDT")
    assert point is not None
    assert point.is_stale is False


@pytest.mark.asyncio
async def test_explicit_disagreement_pct_arg() -> None:
    chain = FallbackMarketDataAdapter(
        [_FakeAdapter("a", _pt("a", 1.00)), _FakeAdapter("b", _pt("b", 1.05))],
        disagreement_pct=0.01,
    )
    point = await chain.get_market_data_point("MATIC/USDT")
    assert point is not None
    assert point.is_stale is True


# --- 2026-06-14: synthetic mock must never corroborate/veto a real venue. ---
# A SKYAI paper position was frozen at ~40% of the book because the only real
# venue (binance_futures 0.355) was cross-checked against the last-resort mock
# adapter's phantom 101.3 → permanently disagreement-stale → never exitable.


def test_mock_source_constant_pinned() -> None:
    # If the mock adapter's source label ever drifts, the exclusion silently
    # breaks — pin it so this test fails loudly instead.
    assert MockMarketDataAdapter().adapter_name == _MOCK_SOURCE


@pytest.mark.asyncio
async def test_mock_does_not_corroborate_real_quote() -> None:
    # The exact SKYAI incident: one real venue + the synthetic mock last resort.
    chain = FallbackMarketDataAdapter(
        [
            _FakeAdapter("bybit", None),
            _FakeAdapter("binance_futures", _pt("binance_futures", 0.35485)),
            _FakeAdapter("okx", None),
            _FakeAdapter(_MOCK_SOURCE, _pt(_MOCK_SOURCE, 101.3)),
        ]
    )
    point = await chain.get_market_data_point("MATIC/USDT")
    assert point is not None
    assert point.is_stale is False  # was True before the fix → position frozen
    assert point.source == "binance_futures"


@pytest.mark.asyncio
async def test_mock_used_only_as_last_resort_when_no_real_provider() -> None:
    chain = FallbackMarketDataAdapter(
        [_FakeAdapter("bybit", None), _FakeAdapter(_MOCK_SOURCE, _pt(_MOCK_SOURCE, 101.3))]
    )
    point = await chain.get_market_data_point("MATIC/USDT")
    assert point is not None
    assert point.source == _MOCK_SOURCE
    assert point.is_stale is False


@pytest.mark.asyncio
async def test_real_disagreement_still_fires_with_mock_present() -> None:
    # MATIC protection must survive: two genuine venues disagree, mock present
    # but irrelevant — the point is still tagged stale.
    chain = FallbackMarketDataAdapter(
        [
            _FakeAdapter("bitmex", _pt("bitmex", 0.40875)),
            _FakeAdapter("cg", _pt("cg", 0.087817)),
            _FakeAdapter(_MOCK_SOURCE, _pt(_MOCK_SOURCE, 101.3)),
        ]
    )
    point = await chain.get_market_data_point("MATIC/USDT")
    assert point is not None
    assert point.is_stale is True
    assert "provider_disagreement" in point.source
