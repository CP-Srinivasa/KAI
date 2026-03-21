"""Tests for CoinGecko read-only market data adapter.

Covers:
- adapter_name identity
- get_price with mocked HTTP responses
- get_ticker with mocked HTTP responses
- get_ohlcv with mocked HTTP responses
- get_market_data_point with staleness detection
- fail-closed behavior (timeout, HTTP errors, malformed data)
- health_check
- unknown symbol handling
- _timeframe_to_days helper
- No trading/write/execution capability
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.market_data.coingecko_adapter import (
    CoinGeckoAdapter,
    _timeframe_to_days,
)
from app.market_data.models import MarketDataPoint

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _adapter(**kwargs: object) -> CoinGeckoAdapter:
    return CoinGeckoAdapter(
        freshness_threshold_seconds=120.0,
        timeout_seconds=5,
        **kwargs,  # type: ignore[arg-type]
    )


def _mock_price_response(
    cg_id: str = "bitcoin",
    price: float = 65000.0,
    volume: float = 1e9,
    change: float = 2.5,
    last_updated: int | None = None,
) -> dict:
    updated = last_updated or int(time.time())
    return {
        cg_id: {
            "usd": price,
            "usd_24h_vol": volume,
            "usd_24h_change": change,
            "last_updated_at": updated,
        }
    }


def _mock_ohlc_response() -> list:
    now_ms = int(time.time() * 1000)
    return [
        [now_ms - 7200_000, 64000, 65500, 63800, 65000],
        [now_ms - 3600_000, 65000, 66000, 64800, 65500],
        [now_ms, 65500, 66200, 65200, 66000],
    ]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_adapter_name() -> None:
    assert _adapter().adapter_name == "coingecko"


# ---------------------------------------------------------------------------
# get_price
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_price_success() -> None:
    adapter = _adapter()
    with patch.object(
        adapter,
        "_get_json",
        new_callable=AsyncMock,
        return_value=_mock_price_response(),
    ):
        price = await adapter.get_price("BTC/USDT")
    assert price == 65000.0


@pytest.mark.asyncio
async def test_get_price_unknown_symbol() -> None:
    adapter = _adapter()
    price = await adapter.get_price("UNKNOWN/PAIR")
    assert price is None


@pytest.mark.asyncio
async def test_get_price_fail_closed_on_http_error() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock, return_value=None,
    ):
        price = await adapter.get_price("BTC/USDT")
    assert price is None


@pytest.mark.asyncio
async def test_get_price_fail_closed_on_zero() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock,
        return_value=_mock_price_response(price=0.0),
    ):
        price = await adapter.get_price("BTC/USDT")
    assert price is None


@pytest.mark.asyncio
async def test_get_price_fail_closed_on_negative() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock,
        return_value=_mock_price_response(price=-100.0),
    ):
        price = await adapter.get_price("BTC/USDT")
    assert price is None


# ---------------------------------------------------------------------------
# get_ticker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ticker_success() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock,
        return_value=_mock_price_response(),
    ):
        t = await adapter.get_ticker("BTC/USDT")
    assert t is not None
    assert t.symbol == "BTC/USDT"
    assert t.last == 65000.0
    assert t.volume_24h == 1e9


@pytest.mark.asyncio
async def test_get_ticker_unknown_symbol() -> None:
    adapter = _adapter()
    t = await adapter.get_ticker("UNKNOWN/PAIR")
    assert t is None


# ---------------------------------------------------------------------------
# get_ohlcv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ohlcv_success() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock,
        return_value=_mock_ohlc_response(),
    ):
        candles = await adapter.get_ohlcv("BTC/USDT", "1h", 10)
    assert len(candles) == 3
    assert candles[0].symbol == "BTC/USDT"
    assert candles[0].open == 64000.0


@pytest.mark.asyncio
async def test_get_ohlcv_empty_on_error() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock, return_value=None,
    ):
        candles = await adapter.get_ohlcv("BTC/USDT")
    assert candles == []


@pytest.mark.asyncio
async def test_get_ohlcv_unknown_symbol() -> None:
    adapter = _adapter()
    candles = await adapter.get_ohlcv("UNKNOWN/PAIR")
    assert candles == []


# ---------------------------------------------------------------------------
# get_market_data_point + staleness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_market_data_point_fresh() -> None:
    adapter = _adapter()
    now = int(time.time())
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock,
        return_value=_mock_price_response(last_updated=now),
    ):
        mdp = await adapter.get_market_data_point("BTC/USDT")
    assert mdp is not None
    assert isinstance(mdp, MarketDataPoint)
    assert mdp.source == "coingecko"
    assert mdp.is_stale is False


@pytest.mark.asyncio
async def test_market_data_point_stale() -> None:
    adapter = CoinGeckoAdapter(freshness_threshold_seconds=10.0)
    old_ts = int(time.time()) - 300  # 5 minutes old
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock,
        return_value=_mock_price_response(last_updated=old_ts),
    ):
        mdp = await adapter.get_market_data_point("BTC/USDT")
    assert mdp is not None
    assert mdp.is_stale is True
    assert mdp.freshness_seconds > 200


@pytest.mark.asyncio
async def test_market_data_point_none_on_error() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock, return_value=None,
    ):
        mdp = await adapter.get_market_data_point("BTC/USDT")
    assert mdp is None


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_success() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock,
        return_value=_mock_price_response(),
    ):
        ok = await adapter.health_check()
    assert ok is True


@pytest.mark.asyncio
async def test_health_check_failure() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json",
        new_callable=AsyncMock, return_value=None,
    ):
        ok = await adapter.health_check()
    assert ok is False


# ---------------------------------------------------------------------------
# _timeframe_to_days helper
# ---------------------------------------------------------------------------


def test_timeframe_to_days_1h_100() -> None:
    assert _timeframe_to_days("1h", 100) == 4


def test_timeframe_to_days_1d_30() -> None:
    assert _timeframe_to_days("1d", 30) == 30


def test_timeframe_to_days_caps_at_365() -> None:
    assert _timeframe_to_days("1d", 500) == 365


def test_timeframe_to_days_minimum_1() -> None:
    assert _timeframe_to_days("1m", 1) >= 1


# ---------------------------------------------------------------------------
# No trading capability
# ---------------------------------------------------------------------------


def test_no_write_methods() -> None:
    """CoinGeckoAdapter must have no write, trade, or execute methods."""
    adapter = _adapter()
    forbidden = [
        "place_order", "create_order", "submit_order",
        "cancel_order", "execute", "trade", "write",
    ]
    for name in forbidden:
        assert not hasattr(adapter, name), (
            f"CoinGeckoAdapter has forbidden method: {name}"
        )


# ---------------------------------------------------------------------------
# Frozen models
# ---------------------------------------------------------------------------


def test_market_data_point_frozen() -> None:
    mdp = MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc=datetime.now(UTC).isoformat(),
        price=65000.0,
        volume_24h=1e9,
        change_pct_24h=2.0,
        source="coingecko",
    )
    with pytest.raises(AttributeError):
        mdp.price = 70000.0  # type: ignore[misc]
