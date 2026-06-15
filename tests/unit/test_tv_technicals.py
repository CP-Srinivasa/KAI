"""WP-I (2026-06-15): per-symbol TV technical-indicator snapshot (webhook-independent)."""

from __future__ import annotations

import pytest

from app.integrations.tradingview.datafeed import DEFAULT_TECH_COLUMNS, TradingViewDatafeed


def _feed(rows: list) -> TradingViewDatafeed:
    f = TradingViewDatafeed(exchange="BYBIT")

    async def _fake_post(body):
        return rows

    f._post = _fake_post  # type: ignore[method-assign]
    return f


def _row(ticker: str, name: str, *vals) -> dict:
    # d = [name, *DEFAULT_TECH_COLUMNS values]
    return {"s": ticker, "d": [name, *vals]}


@pytest.mark.asyncio
async def test_technicals_maps_columns_to_values() -> None:
    # close, change, RSI, MACD.macd, MACD.signal, ADX, EMA50, EMA200, Stoch.K,
    # Recommend.All, Recommend.MA, Recommend.Other
    vals = [66635.1, 1.2, 44.9, -2820.9, -3354.4, 42.4, 70737.3, 78710.1, 46.8, -0.04, -0.27, 0.18]
    out = await _feed([_row("BYBIT:BTCUSDT", "BTCUSDT", *vals)]).technicals(["BTC/USDT"])
    snap = out["BTC/USDT"]
    assert snap["RSI"] == 44.9
    assert snap["MACD.macd"] == -2820.9
    assert snap["ADX"] == 42.4
    assert snap["Recommend.All"] == -0.04
    assert set(snap) == set(DEFAULT_TECH_COLUMNS)


@pytest.mark.asyncio
async def test_unparseable_cell_becomes_none() -> None:
    vals = [100.0, "n/a", 50.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    out = await _feed([_row("BYBIT:SOLUSDT", "SOLUSDT", *vals)]).technicals(["SOL/USDT"])
    assert out["SOL/USDT"]["change"] is None
    assert out["SOL/USDT"]["close"] == 100.0


@pytest.mark.asyncio
async def test_empty_symbols_returns_empty() -> None:
    assert await _feed([]).technicals(["NOSLASH"]) == {}


@pytest.mark.asyncio
async def test_fail_soft_on_empty_response() -> None:
    assert await _feed([]).technicals(["BTC/USDT"]) == {}


@pytest.mark.asyncio
async def test_short_row_is_skipped_gracefully() -> None:
    out = await _feed([{"s": "BYBIT:BTCUSDT", "d": []}]).technicals(["BTC/USDT"])
    assert out == {}
