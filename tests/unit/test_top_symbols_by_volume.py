"""WP-F (2026-06-15): dynamic universe — top symbols by 24h volume.

Sanctioned exchange-data source for the technical screener (no scraping). Pins
the Bybit ranking/filter/limit/fail-soft behaviour and the base default.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.market_data.bybit_adapter import BybitAdapter


def _adapter_with_response(payload: dict[str, Any] | None) -> BybitAdapter:
    a = BybitAdapter()

    async def _fake_get(path: str, params: dict[str, str]) -> dict[str, Any] | None:
        return payload

    a._get = _fake_get  # type: ignore[method-assign]
    return a


_TICKERS = {
    "result": {
        "list": [
            {"symbol": "BTCUSDT", "turnover24h": "1000000000"},
            {"symbol": "ETHUSDT", "turnover24h": "500000000"},
            {"symbol": "SOLUSDT", "turnover24h": "200000000"},
            {"symbol": "FOOBTC", "turnover24h": "999999"},  # non-USDT → excluded
            {"symbol": "ZEROUSDT", "turnover24h": "0"},  # zero volume → excluded
            {"symbol": "BADUSDT", "turnover24h": "n/a"},  # unparseable → excluded
        ]
    }
}


@pytest.mark.asyncio
async def test_ranks_by_turnover_and_canonicalises() -> None:
    a = _adapter_with_response(_TICKERS)
    out = await a.top_symbols_by_volume(10)
    assert out == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]  # sorted desc, USDT only


@pytest.mark.asyncio
async def test_limit_is_honoured() -> None:
    a = _adapter_with_response(_TICKERS)
    out = await a.top_symbols_by_volume(2)
    assert out == ["BTC/USDT", "ETH/USDT"]


@pytest.mark.asyncio
async def test_zero_limit_returns_empty() -> None:
    a = _adapter_with_response(_TICKERS)
    assert await a.top_symbols_by_volume(0) == []


@pytest.mark.asyncio
async def test_fail_soft_on_none_response() -> None:
    a = _adapter_with_response(None)  # transport/api error path
    assert await a.top_symbols_by_volume(10) == []


@pytest.mark.asyncio
async def test_malformed_payload_is_fail_soft() -> None:
    a = _adapter_with_response({"result": {"list": "not_a_list"}})
    assert await a.top_symbols_by_volume(10) == []


@pytest.mark.asyncio
async def test_base_default_returns_empty() -> None:
    """Adapters that don't expose a markets list return [] (no scraping)."""
    from app.market_data.mock_adapter import MockMarketDataAdapter

    out = await MockMarketDataAdapter().top_symbols_by_volume(10)
    assert out == []
