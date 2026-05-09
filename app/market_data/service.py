"""Read-only market data service helpers for CLI/MCP surfaces."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.binance_futures_adapter import BinanceFuturesAdapter
from app.market_data.bitmex_adapter import BitMEXAdapter
from app.market_data.bybit_adapter import BybitAdapter
from app.market_data.coingecko_adapter import CoinGeckoAdapter
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.market_data.models import OHLCV, MarketDataPoint, MarketDataSnapshot, Ticker
from app.market_data.okx_adapter import OKXAdapter

logger = logging.getLogger(__name__)


class FallbackMarketDataAdapter(BaseMarketDataAdapter):
    """Tries each underlying adapter in order, returns the first available.

    Built for the operator-signal bridge (V25-D, 2026-05-05): the premium
    Telegram channel posts Bybit-Futures pairs that include exotic tokens
    CoinGecko does not list. We therefore query Bybit first; if Bybit
    returns no data (symbol not found, transport error, rate limit), we
    fall back to CoinGecko for the well-known majors. Mock is the last
    resort so a smoke-test never crashes for missing market data.

    The chain order is intentional: Bybit is authoritative for the symbols
    the bridge actually sees in production. CoinGecko only covers a subset
    but uses different rate-limit pools, so it adds true redundancy.
    """

    def __init__(self, adapters: list[BaseMarketDataAdapter]) -> None:
        if not adapters:
            raise ValueError("FallbackMarketDataAdapter requires >=1 adapter")
        self._adapters = adapters

    @property
    def adapter_name(self) -> str:
        return "fallback:" + ",".join(a.adapter_name for a in self._adapters)

    async def get_ticker(self, symbol: str) -> Ticker | None:
        for adapter in self._adapters:
            ticker = await adapter.get_ticker(symbol)
            if ticker is not None and ticker.last > 0:
                return ticker
        return None

    async def get_price(self, symbol: str) -> float | None:
        ticker = await self.get_ticker(symbol)
        return ticker.last if ticker is not None else None

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        for adapter in self._adapters:
            data = await adapter.get_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if data:
                return data
        return []

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
        for adapter in self._adapters:
            point = await adapter.get_market_data_point(symbol)
            if point is not None and point.price > 0:
                return point
        return None


def create_market_data_adapter(
    *,
    provider: str,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    api_key: str | None = None,
) -> BaseMarketDataAdapter:
    """Create a market data adapter by provider name.

    Provider values:
    - 'bybit'           : Bybit V5 linear (futures) — broadest premium-channel
                          symbol coverage; primary source.
    - 'binance_futures' : Binance USD-M futures (fapi.binance.com) — full
                          coverage backup with same symbol convention.
    - 'okx'             : OKX V5 perpetual swap (BTC-USDT-SWAP convention) —
                          mainstream-token redundancy.
    - 'bitmex'          : BitMEX instrument ticker (XBT prefix for BTC) —
                          BTC + major-coin redundancy.
    - 'coingecko'       : CoinGecko spot aggregation — broad token list,
                          slower, misses many Bybit-exclusive pairs.
    - 'fallback'        : Try Bybit → Binance Futures → OKX → BitMEX →
                          CoinGecko → Mock. RECOMMENDED for the operator
                          bridge — matches the channel name "Bitmex/Bybit/
                          Futures/OKX Premium Signals" exactly so any signal
                          for any of those venues resolves on the first
                          adapter that knows the symbol.
    - 'mock'            : Synthetic test data only.
    """
    normalized = provider.strip().lower()
    if normalized == "bybit":
        return BybitAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    if normalized in ("binance_futures", "binance-futures", "binancefutures"):
        return BinanceFuturesAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    if normalized == "okx":
        return OKXAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    if normalized == "bitmex":
        return BitMEXAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    if normalized == "coingecko":
        if api_key is None:
            from app.core.settings import get_settings

            api_key = get_settings().coingecko_api_key
        return CoinGeckoAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
            api_key=api_key or None,
        )
    if normalized == "fallback":
        if api_key is None:
            from app.core.settings import get_settings

            api_key = get_settings().coingecko_api_key
        chain: list[BaseMarketDataAdapter] = [
            BybitAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            ),
            BinanceFuturesAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            ),
            OKXAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            ),
            BitMEXAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            ),
            CoinGeckoAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
                api_key=api_key or None,
            ),
            MockMarketDataAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
            ),
        ]
        return FallbackMarketDataAdapter(chain)
    if normalized == "mock":
        logger.warning(
            "market_data_provider=mock: using synthetic mock data. "
            "Set APP_MARKET_DATA_PROVIDER=fallback for real market data."
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
