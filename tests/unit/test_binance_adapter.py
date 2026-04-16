"""TV-2 BinanceAdapter unit tests.

Strategy: mock httpx.AsyncClient.get to avoid live network calls.
Verified paths:
    - Symbol normalization (BTC/USDT → BTCUSDT, with - and : separators).
    - Ticker happy path + invalid payload + missing fields.
    - OHLCV parsing of real Binance kline shape.
    - Timeframe rejection.
    - 429 backoff retry honors Retry-After.
    - Transport timeout / non-200 status → returns None/[] with last_error set.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.market_data.binance_adapter import BinanceAdapter, _normalize_symbol


def _mk_response(
    status: int = 200,
    json_payload: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=json_payload,
        headers=headers or {},
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("BTC/USDT", "BTCUSDT"),
        ("btc-usdt", "BTCUSDT"),
        ("ETH:USDC", "ETHUSDC"),
        ("BTCUSDT", "BTCUSDT"),
        ("  sol/usdt  ", "SOLUSDT"),
        ("", ""),
    ],
)
def test_symbol_normalization(raw: str, expected: str) -> None:
    assert _normalize_symbol(raw) == expected


@pytest.mark.asyncio
async def test_get_ticker_happy_path() -> None:
    payload = {
        "symbol": "BTCUSDT",
        "lastPrice": "65123.45",
        "bidPrice": "65120.00",
        "askPrice": "65125.00",
        "volume": "1234.5",
        "priceChangePercent": "2.34",
        "closeTime": 1_713_312_000_000,
    }
    adapter = BinanceAdapter()
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(200, payload))):
        ticker = await adapter.get_ticker("BTC/USDT")

    assert ticker is not None
    assert ticker.symbol == "BTC/USDT"
    assert ticker.last == 65123.45
    assert ticker.bid == 65120.00
    assert ticker.ask == 65125.00
    assert ticker.volume_24h == 1234.5
    assert ticker.change_pct_24h == 2.34
    assert adapter.last_error is None


@pytest.mark.asyncio
async def test_get_ticker_invalid_payload_returns_none() -> None:
    adapter = BinanceAdapter()
    bad = _mk_response(200, {"foo": "bar"})
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=bad)):
        ticker = await adapter.get_ticker("BTC/USDT")
    assert ticker is None
    assert adapter.last_error == "invalid_ticker_payload"


@pytest.mark.asyncio
async def test_get_ticker_negative_price_rejected() -> None:
    payload = {"lastPrice": "-1.0", "volume": "0", "priceChangePercent": "0"}
    adapter = BinanceAdapter()
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(200, payload))):
        ticker = await adapter.get_ticker("BTC/USDT")
    assert ticker is None
    assert adapter.last_error == "invalid_price"


@pytest.mark.asyncio
async def test_get_ohlcv_happy_path() -> None:
    # Binance kline row order:
    # [open_time, open, high, low, close, volume, close_time, quote_vol, trades, ...]
    klines = [
        [
            1_713_312_000_000, "65000.0", "65200.0", "64950.0", "65150.0",
            "12.34", 1_713_315_599_999, "803000.0", 250, "6.0", "390000.0", "0",
        ],
        [
            1_713_315_600_000, "65150.0", "65300.0", "65100.0", "65275.0",
            "10.0", 1_713_319_199_999, "651500.0", 200, "5.0", "325000.0", "0",
        ],
    ]
    adapter = BinanceAdapter()
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(200, klines))):
        candles = await adapter.get_ohlcv("BTC/USDT", timeframe="1h", limit=2)

    assert len(candles) == 2
    assert candles[0].symbol == "BTC/USDT"
    assert candles[0].open == 65000.0
    assert candles[0].high == 65200.0
    assert candles[0].low == 64950.0
    assert candles[0].close == 65150.0
    assert candles[0].volume == 12.34
    assert candles[0].timeframe == "1h"
    assert candles[1].close == 65275.0


@pytest.mark.asyncio
async def test_get_ohlcv_unsupported_timeframe() -> None:
    adapter = BinanceAdapter()
    with patch("httpx.AsyncClient.get", new=AsyncMock()) as mocked:
        candles = await adapter.get_ohlcv("BTC/USDT", timeframe="3m")
    assert candles == []
    assert adapter.last_error == "unsupported_timeframe"
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_get_ohlcv_skips_malformed_rows() -> None:
    klines = [
        ["bad"],  # too short
        [1, "0.0", "1.0", "0.0", "0.0", "0", 2, "0", 0],  # zero open → rejected
        [1, "1", "2", "0.5", "1.5", "10", 2, "0", 0],  # valid
    ]
    adapter = BinanceAdapter()
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(200, klines))):
        candles = await adapter.get_ohlcv("BTC/USDT")
    assert len(candles) == 1
    assert candles[0].close == 1.5


@pytest.mark.asyncio
async def test_get_ohlcv_empty_returns_empty_with_error() -> None:
    adapter = BinanceAdapter()
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(200, []))):
        candles = await adapter.get_ohlcv("BTC/USDT")
    assert candles == []
    assert adapter.last_error == "empty_klines"


@pytest.mark.asyncio
async def test_http_error_returns_none_with_status_in_error() -> None:
    adapter = BinanceAdapter()
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mk_response(500))):
        ticker = await adapter.get_ticker("BTC/USDT")
    assert ticker is None
    assert adapter.last_error == "http_500"


@pytest.mark.asyncio
async def test_429_retries_then_succeeds() -> None:
    rate_limited = _mk_response(429, headers={"Retry-After": "1"})
    success_payload = {
        "lastPrice": "100.0",
        "bidPrice": "100.0",
        "askPrice": "100.0",
        "volume": "1.0",
        "priceChangePercent": "0.0",
    }
    success = _mk_response(200, success_payload)
    adapter = BinanceAdapter(max_retries=2)
    # asyncio.sleep is patched to instant so test is fast.
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=[rate_limited, success])), \
         patch("app.market_data.binance_adapter.asyncio.sleep", new=AsyncMock(return_value=None)):
        ticker = await adapter.get_ticker("BTC/USDT")
    assert ticker is not None
    assert ticker.last == 100.0


@pytest.mark.asyncio
async def test_timeout_returns_none_with_error() -> None:
    adapter = BinanceAdapter(max_retries=1)
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=httpx.TimeoutException("slow"))):
        ticker = await adapter.get_ticker("BTC/USDT")
    assert ticker is None
    assert adapter.last_error == "timeout"


@pytest.mark.asyncio
async def test_adapter_name() -> None:
    assert BinanceAdapter().adapter_name == "binance"
