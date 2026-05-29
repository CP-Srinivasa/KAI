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
from app.market_data.models import OHLCV, MarketDataPoint, Ticker
from app.market_data.service import FallbackMarketDataAdapter


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
