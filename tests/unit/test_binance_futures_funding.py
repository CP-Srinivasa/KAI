"""Funding-Rate-Methode des Binance-Futures-Adapters.

Mock-Pattern wie test_binance_adapter.py: httpx.AsyncClient.get gepatcht,
damit kein Live-Traffic stattfindet.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.market_data.binance_futures_adapter import BinanceFuturesAdapter


def _mk_response(status: int = 200, json_payload: Any = None) -> httpx.Response:
    return httpx.Response(status_code=status, json=json_payload)


@pytest.mark.asyncio
async def test_get_funding_rate_happy_path() -> None:
    adapter = BinanceFuturesAdapter()
    payload = {
        "symbol": "BTCUSDT",
        "markPrice": "65000.5",
        "indexPrice": "65010.2",
        "lastFundingRate": "0.0001",  # 1 bp / 8h
        "time": 1_715_265_600_000,
        "nextFundingTime": 1_715_294_400_000,
    }
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(200, payload))):
        result = await adapter.get_funding_rate("BTC/USDT")
    assert result is not None
    assert result.symbol == "BTC/USDT"
    assert result.rate == pytest.approx(0.0001)
    assert result.mark_price == pytest.approx(65000.5)
    assert result.index_price == pytest.approx(65010.2)
    assert result.source == "binance_futures"
    assert result.next_funding_time_utc is not None


@pytest.mark.asyncio
async def test_get_funding_rate_negative_rate_passes_through() -> None:
    adapter = BinanceFuturesAdapter()
    payload = {"symbol": "BTCUSDT", "lastFundingRate": "-0.00025", "time": 1_715_265_600_000}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(200, payload))):
        result = await adapter.get_funding_rate("BTC/USDT")
    assert result is not None
    assert result.rate == pytest.approx(-0.00025)


@pytest.mark.asyncio
async def test_get_funding_rate_empty_symbol_returns_none() -> None:
    adapter = BinanceFuturesAdapter()
    result = await adapter.get_funding_rate("")
    assert result is None
    assert adapter.last_error == "empty_symbol"


@pytest.mark.asyncio
async def test_get_funding_rate_404_returns_none() -> None:
    adapter = BinanceFuturesAdapter()
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(404))):
        result = await adapter.get_funding_rate("BOGUS/USDT")
    assert result is None
    assert adapter.last_error == "symbol_not_found"


@pytest.mark.asyncio
async def test_get_funding_rate_rate_limited_returns_none() -> None:
    adapter = BinanceFuturesAdapter()
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(429))):
        result = await adapter.get_funding_rate("BTC/USDT")
    assert result is None
    assert adapter.last_error == "rate_limited"


@pytest.mark.asyncio
async def test_get_funding_rate_transport_error_returns_none() -> None:
    adapter = BinanceFuturesAdapter()
    with patch(
        "httpx.AsyncClient.get",
        new=AsyncMock(side_effect=httpx.ConnectError("boom")),
    ):
        result = await adapter.get_funding_rate("BTC/USDT")
    assert result is None
    assert adapter.last_error and adapter.last_error.startswith("transport_error")


@pytest.mark.asyncio
async def test_get_funding_rate_unexpected_payload_returns_none() -> None:
    adapter = BinanceFuturesAdapter()
    # Kein lastFundingRate-Feld
    payload = {"symbol": "BTCUSDT", "markPrice": "65000"}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(200, payload))):
        result = await adapter.get_funding_rate("BTC/USDT")
    assert result is None
    assert adapter.last_error == "unexpected_payload"
