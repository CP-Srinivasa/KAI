"""Unit tests for market data adapters."""

from __future__ import annotations

import pytest

from app.market_data.mock_adapter import MockMarketDataAdapter


@pytest.mark.asyncio
async def test_mock_get_price_returns_positive():
    adapter = MockMarketDataAdapter()
    price = await adapter.get_price("BTC/USDT")
    assert price is not None
    assert price > 0


@pytest.mark.asyncio
async def test_mock_ticker_fields():
    adapter = MockMarketDataAdapter()
    ticker = await adapter.get_ticker("ETH/USDT")
    assert ticker is not None
    assert ticker.symbol == "ETH/USDT"
    assert ticker.bid > 0
    assert ticker.ask > ticker.bid
    assert ticker.last > 0
    assert ticker.volume_24h > 0


@pytest.mark.asyncio
async def test_mock_ohlcv_count():
    adapter = MockMarketDataAdapter()
    candles = await adapter.get_ohlcv("BTC/USDT", timeframe="1h", limit=24)
    assert len(candles) == 24
    for c in candles:
        assert c.high >= c.close >= 0
        assert c.high >= c.open >= 0
        assert c.low <= c.open
        assert c.low <= c.close
        assert c.volume > 0


@pytest.mark.asyncio
async def test_mock_deterministic_price():
    adapter = MockMarketDataAdapter()
    p1 = await adapter.get_price("BTC/USDT")
    p2 = await adapter.get_price("BTC/USDT")
    assert p1 == p2  # deterministic


@pytest.mark.asyncio
async def test_mock_unknown_symbol_returns_default():
    adapter = MockMarketDataAdapter()
    price = await adapter.get_price("UNKNOWN/USDT")
    assert price is not None
    # Base price is 100.0 with sinusoidal variation; amplitude=2% → range [98, 102]
    assert 98.0 <= price <= 102.0


@pytest.mark.asyncio
async def test_mock_health_check():
    adapter = MockMarketDataAdapter()
    healthy = await adapter.health_check()
    assert healthy is True


@pytest.mark.asyncio
async def test_mock_market_data_point():
    adapter = MockMarketDataAdapter()
    point = await adapter.get_market_data_point("SOL/USDT")
    assert point is not None
    assert point.source == "mock"
    assert point.price > 0
    assert not point.is_stale
