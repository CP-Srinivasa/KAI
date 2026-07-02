"""Tests for TradingView chart-symbol normalization in _split_ticker (2026-06-18).

Operator alerts on perp/exchange-prefixed charts (BTCUSD.P, BYBIT:SOLUSDT); these
must still resolve to the crypto base asset so one universal "ticker":"{{ticker}}"
template works across pairs. Dated-futures codes (SOLM2026) stay unmapped on
purpose. Also pins DASH into the CoinGecko base map (operator's tradeable list).
"""

from __future__ import annotations

from app.alerts.tv_bridge import _split_ticker
from app.market_data.coingecko_adapter import _BASE_ASSET_TO_COINGECKO


def test_plain_spot_unchanged() -> None:
    assert _split_ticker("XRPUSD") == ("XRP", "USD")
    assert _split_ticker("BTCUSDT") == ("BTC", "USDT")


def test_perp_suffix_stripped() -> None:
    assert _split_ticker("BTCUSD.P") == ("BTC", "USD")
    assert _split_ticker("LTCUSD.P") == ("LTC", "USD")
    assert _split_ticker("ETHUSDT.PERP") == ("ETH", "USDT")


def test_exchange_prefix_stripped() -> None:
    assert _split_ticker("BYBIT:SOLUSDT") == ("SOL", "USDT")


def test_prefix_and_perp_combined() -> None:
    assert _split_ticker("BYBIT:BTCUSDT.P") == ("BTC", "USDT")


def test_dated_futures_stay_unmapped() -> None:
    # No clean base/quote → must NOT guess (avoids wrong-asset trades).
    assert _split_ticker("SOLM2026") is None
    assert _split_ticker("SLRN2026") is None


def test_dash_in_base_map_and_resolves() -> None:
    assert _BASE_ASSET_TO_COINGECKO.get("DASH") == "dash"
    base, quote = _split_ticker("DASHUSD")  # type: ignore[misc]
    assert base == "DASH"
    assert base in _BASE_ASSET_TO_COINGECKO
