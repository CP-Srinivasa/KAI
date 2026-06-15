"""WP-H.1 (2026-06-15): TV ratings for an exact symbol set (universe join)."""

from __future__ import annotations

import pytest

from app.integrations.tradingview.datafeed import TradingViewDatafeed


def _feed(rows: list) -> TradingViewDatafeed:
    f = TradingViewDatafeed(exchange="BYBIT")

    async def _fake_post(body):
        return rows

    f._post = _fake_post  # type: ignore[method-assign]
    return f


def test_to_ticker_maps_canonical_to_exchange_prefixed() -> None:
    f = TradingViewDatafeed(exchange="BYBIT")
    assert f._to_ticker("ADA/USDT") == "BYBIT:ADAUSDT"
    assert f._to_ticker("NOSLASH") is None
    assert f._to_ticker("/USDT") is None


@pytest.mark.asyncio
async def test_ratings_for_joins_exact_symbols() -> None:
    rows = [
        {"s": "BYBIT:ADAUSDT", "d": ["ADAUSDT", -0.11]},
        {"s": "BYBIT:SOLUSDT", "d": ["SOLUSDT", -0.18]},
        {"s": "BYBIT:WONKY", "d": ["WONKY"]},  # malformed → skipped
    ]
    out = await _feed(rows).ratings_for(["ADA/USDT", "SOL/USDT", "NOSLASH"])
    assert out == {"ADA/USDT": -0.11, "SOL/USDT": -0.18}


@pytest.mark.asyncio
async def test_ratings_for_empty_symbols_returns_empty() -> None:
    out = await _feed([{"s": "BYBIT:ADAUSDT", "d": ["ADAUSDT", -0.11]}]).ratings_for(["NOSLASH"])
    assert out == {}


@pytest.mark.asyncio
async def test_ratings_for_fail_soft_on_empty_response() -> None:
    assert await _feed([]).ratings_for(["ADA/USDT"]) == {}
