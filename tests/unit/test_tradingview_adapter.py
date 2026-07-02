"""Tests for the TradingView market-data adapter + its additive cascade wiring.

Reduces sole-CoinGecko dependence: resolves a symbol's last price from the TV
scanner, fail-soft, inserted ADDITIVELY (before Mock) behind a default-off flag.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.market_data.service import create_market_data_adapter
from app.market_data.tradingview_adapter import TradingViewMarketDataAdapter


class _FakeFeed:
    def __init__(self, result: dict[str, dict[str, float | None]] | Exception) -> None:
        self._result = result
        self.calls: list[list[str]] = []

    async def technicals(self, symbols, *, columns=("close",)):  # type: ignore[no-untyped-def]
        self.calls.append(list(symbols))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _adapter(result):
    return TradingViewMarketDataAdapter(datafeed=_FakeFeed(result))  # type: ignore[arg-type]


def test_adapter_name() -> None:
    assert _adapter({}).adapter_name == "tradingview"


@pytest.mark.asyncio
async def test_get_ticker_and_price_from_close() -> None:
    a = _adapter({"BTC/USDT": {"close": 65000.0}})
    t = await a.get_ticker("BTC/USDT")
    assert t is not None
    assert t.symbol == "BTC/USDT"
    assert t.last == 65000.0 and t.bid == 65000.0 and t.ask == 65000.0
    assert await a.get_price("BTC/USDT") == 65000.0


@pytest.mark.asyncio
async def test_missing_slash_returns_none_without_calling_feed() -> None:
    feed = _FakeFeed({"BTCUSDT": {"close": 1.0}})
    a = TradingViewMarketDataAdapter(datafeed=feed)  # type: ignore[arg-type]
    assert await a.get_ticker("BTCUSDT") is None
    assert feed.calls == []  # never queried the scanner


@pytest.mark.asyncio
async def test_missing_or_invalid_close_returns_none() -> None:
    assert await _adapter({"BTC/USDT": {"close": None}}).get_ticker("BTC/USDT") is None
    assert await _adapter({"BTC/USDT": {"close": 0.0}}).get_ticker("BTC/USDT") is None
    assert await _adapter({}).get_ticker("BTC/USDT") is None


@pytest.mark.asyncio
async def test_feed_error_is_fail_soft() -> None:
    a = _adapter(RuntimeError("scanner down"))
    assert await a.get_ticker("BTC/USDT") is None  # never raises into the chain


@pytest.mark.asyncio
async def test_get_ohlcv_empty() -> None:
    assert await _adapter({}).get_ohlcv("BTC/USDT") == []


def _fake_settings(flag: bool) -> SimpleNamespace:
    return SimpleNamespace(
        coingecko_api_key="",
        tradingview_price_fallback_enabled=flag,
        tradingview=SimpleNamespace(datafeed_exchange="BYBIT"),
    )


def test_cascade_includes_tradingview_only_when_flag_on(monkeypatch) -> None:
    monkeypatch.setattr("app.core.settings.get_settings", lambda: _fake_settings(True))
    on = create_market_data_adapter(provider="fallback")
    assert "tradingview" in on.adapter_name
    # additive: TV sits before the synthetic mock last-resort
    assert on.adapter_name.index("tradingview") < on.adapter_name.index("mock")

    monkeypatch.setattr("app.core.settings.get_settings", lambda: _fake_settings(False))
    off = create_market_data_adapter(provider="fallback")
    assert "tradingview" not in off.adapter_name
