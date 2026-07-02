"""WP-G / Track 2 (2026-06-15): unofficial TradingView datafeed (isolated).

Pins parsing of the scanner response, canonicalisation, the Recommend.All rating
labels, and fail-soft behaviour. No network — _scan is stubbed.
"""

from __future__ import annotations

import pytest

from app.integrations.tradingview.datafeed import (
    TradingViewDatafeed,
    _canonical,
    rating_label,
)

# scanner row shape: {"s": "BYBIT:BTCUSDT", "d": [name, close, change, Recommend.All, volume]}
_ROWS = [
    {"s": "BYBIT:BTCUSDT", "d": ["BTCUSDT", 67000.0, 1.2, 0.62, 9e9]},
    {"s": "BYBIT:ETHUSDT", "d": ["ETHUSDT", 3500.0, -0.4, 0.15, 5e9]},
    {"s": "BYBIT:FOOBTC", "d": ["FOOBTC", 1.0, 0.0, 0.0, 1e9]},  # non-USDT → dropped
    {"s": "BYBIT:BTCUSDT", "d": ["BTCUSDT", 67000.0, 1.2, 0.62, 9e9]},  # dup → dropped
    {"s": "BYBIT:WONKYUSDT", "d": ["WONKYUSDT"]},  # malformed (d too short) → dropped
]


def _feed_with_rows(rows: list) -> TradingViewDatafeed:
    f = TradingViewDatafeed()

    async def _fake_scan(columns, *, limit, sort_by):
        return rows

    f._scan = _fake_scan  # type: ignore[method-assign]
    return f


def test_canonical_normalises_exchange_prefix_and_usdt() -> None:
    assert _canonical("BYBIT:BTCUSDT") == "BTC/USDT"
    assert _canonical("ETHUSDT") == "ETH/USDT"
    assert _canonical("FOOBTC") is None  # non-USDT
    assert _canonical("USDT") is None  # too short


@pytest.mark.asyncio
async def test_top_rows_parses_and_dedupes() -> None:
    rows = await _feed_with_rows(_ROWS).top_rows(limit=50)
    assert [r.symbol for r in rows] == ["BTC/USDT", "ETH/USDT"]
    btc = rows[0]
    assert btc.raw_symbol == "BTCUSDT"
    assert btc.close == 67000.0
    assert btc.change_pct == 1.2
    assert btc.rating == 0.62


@pytest.mark.asyncio
async def test_top_symbols_by_volume_shape_matches_adapters() -> None:
    syms = await _feed_with_rows(_ROWS).top_symbols_by_volume(50)
    assert syms == ["BTC/USDT", "ETH/USDT"]


@pytest.mark.asyncio
async def test_fail_soft_on_empty_scan() -> None:
    assert await _feed_with_rows([]).top_rows(limit=50) == []
    assert await _feed_with_rows([]).top_symbols_by_volume(50) == []


def test_rating_labels_follow_tv_convention() -> None:
    assert rating_label(0.7) == "strong_buy"
    assert rating_label(0.2) == "buy"
    assert rating_label(0.0) == "neutral"
    assert rating_label(-0.2) == "sell"
    assert rating_label(-0.7) == "strong_sell"
    assert rating_label(None) == "unknown"
