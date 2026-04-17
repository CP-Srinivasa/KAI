"""Read-only market data service helpers for CLI/MCP surfaces."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.coingecko_adapter import CoinGeckoAdapter
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.market_data.models import MarketDataSnapshot

logger = logging.getLogger(__name__)


def create_market_data_adapter(
    *,
    provider: str,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    api_key: str | None = None,
) -> BaseMarketDataAdapter:
    """Create a market data adapter by provider name.

    For 'coingecko': when `api_key` is omitted, falls back to
    `AppSettings.coingecko_api_key`. A non-empty key activates the paid
    pro-api endpoint (250 req/min, 100k/mo); empty key = free tier.
    Using 'mock' is only appropriate for tests/dev — a WARNING is logged.
    """
    normalized = provider.strip().lower()
    if normalized == "coingecko":
        if api_key is None:
            from app.core.settings import get_settings

            api_key = get_settings().coingecko_api_key
        return CoinGeckoAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
            api_key=api_key or None,
        )
    if normalized == "mock":
        logger.warning(
            "market_data_provider=mock: using synthetic mock data. "
            "Set APP_MARKET_DATA_PROVIDER=coingecko for real market data."
        )
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
