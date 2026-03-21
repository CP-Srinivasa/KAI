"""Mock market data adapter — deterministic, zero-dependency, for testing/paper trading."""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.models import OHLCV, MarketDataPoint, Ticker

# Deterministic "base prices" for common symbols
_BASE_PRICES: dict[str, float] = {
    "BTC/USDT": 65000.0,
    "ETH/USDT": 3200.0,
    "BNB/USDT": 400.0,
    "SOL/USDT": 150.0,
    "AAPL": 185.0,
    "MSFT": 420.0,
    "SPY": 520.0,
}

_TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440,
}


def _mock_price(symbol: str, offset_minutes: float = 0.0, amplitude_pct: float = 2.0) -> float:
    """Generate deterministic sinusoidal price for testing."""
    base = _BASE_PRICES.get(symbol, 100.0)
    # Period: 24h = 1440 minutes; phase based on symbol hash
    period = 1440.0
    phase = hash(symbol) % 360
    t = (offset_minutes + phase) / period * 2 * math.pi
    variation = base * (amplitude_pct / 100) * math.sin(t)
    return round(base + variation, 2)


class MockMarketDataAdapter(BaseMarketDataAdapter):
    """
    Deterministic mock adapter. Produces realistic-looking price data.
    Used for paper trading, unit tests, and development without live exchange access.

    Price model: sinusoidal variation around base price (configurable amplitude).
    No randomness — outputs are deterministic given same inputs.
    """

    def __init__(
        self,
        *,
        amplitude_pct: float = 2.0,
        spread_pct: float = 0.1,
        volume_base: float = 1_000_000.0,
        freshness_threshold_seconds: float = 30.0,
    ) -> None:
        self._amplitude_pct = amplitude_pct
        self._spread_pct = spread_pct
        self._volume_base = volume_base
        self._freshness_threshold = freshness_threshold_seconds

    @property
    def adapter_name(self) -> str:
        return "mock"

    async def get_price(self, symbol: str) -> float | None:
        return _mock_price(symbol, amplitude_pct=self._amplitude_pct)

    async def get_ticker(self, symbol: str) -> Ticker | None:
        last = _mock_price(symbol, amplitude_pct=self._amplitude_pct)
        spread = last * (self._spread_pct / 100)
        prev = _mock_price(symbol, offset_minutes=-60.0, amplitude_pct=self._amplitude_pct)
        change_pct = ((last - prev) / prev * 100) if prev > 0 else 0.0
        return Ticker(
            symbol=symbol,
            timestamp_utc=datetime.now(UTC).isoformat(),
            bid=round(last - spread / 2, 4),
            ask=round(last + spread / 2, 4),
            last=last,
            volume_24h=self._volume_base * (1 + 0.1 * math.sin(hash(symbol) % 100)),
            change_pct_24h=round(change_pct, 4),
        )

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        minutes_per_candle = _TIMEFRAME_MINUTES.get(timeframe, 60)
        candles: list[OHLCV] = []
        now = datetime.now(UTC)

        for i in range(limit - 1, -1, -1):
            offset = -(i * minutes_per_candle)
            ts = now + timedelta(minutes=offset)
            open_p = _mock_price(symbol, offset_minutes=offset, amplitude_pct=self._amplitude_pct)
            close_p = _mock_price(
                symbol,
                offset_minutes=offset + minutes_per_candle,
                amplitude_pct=self._amplitude_pct,
            )
            high_p = max(open_p, close_p) * 1.003
            low_p = min(open_p, close_p) * 0.997
            volume = (
                self._volume_base / (1440 / minutes_per_candle) * (0.8 + 0.4 * abs(math.sin(i)))
            )
            candles.append(OHLCV(
                symbol=symbol,
                timestamp_utc=ts.isoformat(),
                timeframe=timeframe,
                open=round(open_p, 4),
                high=round(high_p, 4),
                low=round(low_p, 4),
                close=round(close_p, 4),
                volume=round(volume, 2),
            ))
        return candles

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
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
            is_stale=False,
            freshness_seconds=0.0,
        )
