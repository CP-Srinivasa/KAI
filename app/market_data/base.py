"""Base interface for market data adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from app.market_data.models import OHLCV, MarketDataPoint, MarketDataSnapshot, Ticker


class BaseMarketDataAdapter(ABC):
    """
    Abstract interface for all market data sources.

    All implementations must:
    - Never raise on data fetch errors (return None or empty list)
    - Tag data with is_stale=True if beyond freshness threshold
    - Validate data before returning (no NaN, negative prices, etc.)
    """

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Unique adapter identifier."""
        ...

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker | None:
        """Get current ticker for symbol. Returns None on error."""
        ...

    @abstractmethod
    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[OHLCV]:
        """Get OHLCV candles. Returns empty list on error."""
        ...

    @abstractmethod
    async def get_price(self, symbol: str) -> float | None:
        """Get current price. Returns None on error."""
        ...

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
        """Get a MarketDataPoint summary. Default: derives from ticker."""
        ticker = await self.get_ticker(symbol)
        if ticker is None:
            return None
        return MarketDataPoint(
            symbol=ticker.symbol,
            timestamp_utc=ticker.timestamp_utc,
            price=ticker.last,
            volume_24h=ticker.volume_24h,
            change_pct_24h=ticker.change_pct_24h,
            source=self.adapter_name,
        )

    async def health_check(self) -> bool:
        """Returns True if adapter is reachable and returning valid data."""
        try:
            ticker = await self.get_ticker("BTC/USDT")
            return ticker is not None and ticker.last > 0
        except Exception:
            return False

    async def get_market_data_snapshot(self, symbol: str) -> MarketDataSnapshot:
        """Return a read-only snapshot with explicit availability and stale metadata."""
        retrieved_at = datetime.now(UTC).isoformat()
        try:
            point = await self.get_market_data_point(symbol)
        except Exception as exc:  # noqa: BLE001
            return MarketDataSnapshot(
                symbol=symbol,
                provider=self.adapter_name,
                retrieved_at_utc=retrieved_at,
                source_timestamp_utc=None,
                price=None,
                is_stale=True,
                freshness_seconds=None,
                available=False,
                error=f"adapter_error:{exc}",
            )

        if point is None:
            return MarketDataSnapshot(
                symbol=symbol,
                provider=self.adapter_name,
                retrieved_at_utc=retrieved_at,
                source_timestamp_utc=None,
                price=None,
                is_stale=True,
                freshness_seconds=None,
                available=False,
                error="market_data_unavailable",
            )

        if point.price <= 0:
            return MarketDataSnapshot(
                symbol=point.symbol,
                provider=self.adapter_name,
                retrieved_at_utc=retrieved_at,
                source_timestamp_utc=point.timestamp_utc,
                price=None,
                is_stale=True,
                freshness_seconds=point.freshness_seconds,
                available=False,
                error="invalid_price",
            )

        return MarketDataSnapshot(
            symbol=point.symbol,
            provider=self.adapter_name,
            retrieved_at_utc=retrieved_at,
            source_timestamp_utc=point.timestamp_utc,
            price=point.price,
            is_stale=point.is_stale,
            freshness_seconds=point.freshness_seconds,
            available=True,
            error=("stale_data" if point.is_stale else None),
        )
