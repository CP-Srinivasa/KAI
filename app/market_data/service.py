"""Read-only market data service helpers for CLI/MCP surfaces."""
from __future__ import annotations

from datetime import UTC, datetime

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.coingecko_adapter import CoinGeckoAdapter
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.market_data.models import MarketDataSnapshot


def create_market_data_adapter(
    *,
    provider: str,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> BaseMarketDataAdapter:
    normalized = provider.strip().lower()
    if normalized == "coingecko":
        return CoinGeckoAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    if normalized == "mock":
        return MockMarketDataAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
        )
    raise ValueError(f"unsupported_provider:{provider}")


async def get_market_data_snapshot(
    *,
    symbol: str,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> MarketDataSnapshot:
    retrieved_at = datetime.now(UTC).isoformat()
    try:
        adapter = create_market_data_adapter(
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        return MarketDataSnapshot(
            symbol=symbol,
            provider=provider,
            retrieved_at_utc=retrieved_at,
            source_timestamp_utc=None,
            price=None,
            is_stale=True,
            freshness_seconds=None,
            available=False,
            error=str(exc),
        )

    return await adapter.get_market_data_snapshot(symbol)
