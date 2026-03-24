"""MCP tests for Sprint 39 market data read-only surface."""

from __future__ import annotations

import json

import pytest

from app.agents.mcp_server import (
    get_market_data_quote,
    get_mcp_capabilities,
    get_mcp_tool_inventory,
)
from app.market_data.models import MarketDataSnapshot


@pytest.mark.asyncio
async def test_mcp_inventory_includes_market_data_quote() -> None:
    inventory = get_mcp_tool_inventory()
    assert "get_market_data_quote" in inventory["canonical_read_tools"]
    assert "get_market_data_quote" not in inventory["guarded_write_tools"]


@pytest.mark.asyncio
async def test_mcp_capabilities_list_market_data_quote_as_read_tool() -> None:
    payload = json.loads(await get_mcp_capabilities())
    assert "get_market_data_quote" in payload["read_tools"]
    assert "get_market_data_quote" not in payload["write_tools"]


@pytest.mark.asyncio
async def test_get_market_data_quote_returns_read_only_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_snapshot(**kwargs):  # noqa: ANN003
        assert kwargs["symbol"] == "BTC/USDT"
        assert kwargs["provider"] == "coingecko"
        return MarketDataSnapshot(
            symbol="BTC/USDT",
            provider="coingecko",
            retrieved_at_utc="2026-03-21T12:00:00+00:00",
            source_timestamp_utc="2026-03-21T11:59:30+00:00",
            price=65000.0,
            is_stale=False,
            freshness_seconds=30.0,
            available=True,
            error=None,
        )

    monkeypatch.setattr("app.market_data.service.get_market_data_snapshot", fake_snapshot)

    result = await get_market_data_quote(symbol="BTC/USDT", provider="coingecko")

    assert result["report_type"] == "market_data_snapshot"
    assert result["symbol"] == "BTC/USDT"
    assert result["provider"] == "coingecko"
    assert result["available"] is True
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_market_data_quote_fail_closed_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_snapshot(**kwargs):  # noqa: ANN003
        return MarketDataSnapshot(
            symbol=kwargs["symbol"],
            provider=kwargs["provider"],
            retrieved_at_utc="2026-03-21T12:00:00+00:00",
            source_timestamp_utc=None,
            price=None,
            is_stale=True,
            freshness_seconds=None,
            available=False,
            error="timeout",
        )

    monkeypatch.setattr("app.market_data.service.get_market_data_snapshot", fake_snapshot)

    result = await get_market_data_quote(symbol="BTC/USDT", provider="coingecko")

    assert result["available"] is False
    assert result["error"] == "timeout"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
