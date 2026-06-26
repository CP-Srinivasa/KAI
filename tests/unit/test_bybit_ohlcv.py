"""Bybit market-data OHLCV (kline) — first real consumer is the Momentum-Universe.

Pins: timeframe→interval mapping, symbol normalization, newest-first→ascending
re-ordering, validation (drop non-finite / non-positive / short rows), and
fail-soft ([] on unsupported timeframe / dead source).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.market_data.bybit_adapter import BybitAdapter


def _adapter_with_response(payload: dict[str, Any] | None) -> BybitAdapter:
    a = BybitAdapter()

    async def _fake_get(path: str, params: dict[str, str]) -> dict[str, Any] | None:
        # Pin the request contract for daily klines.
        assert path == "/v5/market/kline"
        assert params["category"] == "linear"
        a.last_seen_params = params  # type: ignore[attr-defined]
        return payload

    a._get = _fake_get  # type: ignore[method-assign]
    return a


# Bybit returns klines NEWEST-FIRST: [start_ms, o, h, l, c, volume, turnover].
_KLINES = {
    "result": {
        "list": [
            ["1700200000000", "102", "103", "101", "102.5", "10", "1000"],
            ["1700100000000", "101", "102", "100", "101.5", "11", "1100"],
            ["1700000000000", "100", "101", "99", "100.5", "12", "1200"],
        ]
    }
}


@pytest.mark.asyncio
async def test_parses_and_orders_ascending() -> None:
    a = _adapter_with_response(_KLINES)
    out = await a.get_ohlcv("BTC/USDT", "1d", 3)
    assert [c.close for c in out] == [100.5, 101.5, 102.5]  # oldest → newest
    assert out[0].symbol == "BTC/USDT"
    assert out[0].timeframe == "1d"
    assert out[-1].high == 103.0
    assert a.last_seen_params["interval"] == "D"  # type: ignore[attr-defined]
    assert a.last_seen_params["symbol"] == "BTCUSDT"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_intraday_timeframe_maps_to_minutes() -> None:
    a = _adapter_with_response(_KLINES)
    await a.get_ohlcv("ETH/USDT", "1h", 5)
    assert a.last_seen_params["interval"] == "60"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unsupported_timeframe_is_fail_soft() -> None:
    a = BybitAdapter()
    out = await a.get_ohlcv("BTC/USDT", "7d", 10)
    assert out == []
    assert "unsupported_timeframe" in (a.last_error or "")


@pytest.mark.asyncio
async def test_dead_source_returns_empty() -> None:
    a = _adapter_with_response(None)
    assert await a.get_ohlcv("BTC/USDT", "1d", 5) == []


@pytest.mark.asyncio
async def test_invalid_rows_are_dropped() -> None:
    payload = {
        "result": {
            "list": [
                ["1700200000000", "102", "103", "101", "102.5", "10", "1000"],
                ["1700100000000", "0", "0", "0", "0", "0", "0"],  # non-positive → drop
                ["1700000000000", "x", "y", "z", "w", "v", "u"],  # unparseable → drop
                ["1699900000000"],  # too short → drop
            ]
        }
    }
    a = _adapter_with_response(payload)
    out = await a.get_ohlcv("BTC/USDT", "1d", 4)
    assert len(out) == 1
    assert out[0].close == 102.5


@pytest.mark.asyncio
async def test_empty_symbol_is_fail_soft() -> None:
    a = BybitAdapter()
    out = await a.get_ohlcv("", "1d", 5)
    assert out == []
    assert a.last_error == "empty_symbol"
