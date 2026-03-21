"""Market data typed models."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OHLCV:
    """Single OHLCV candle."""
    symbol: str
    timestamp_utc: str
    timeframe: str    # "1m" | "5m" | "1h" | "4h" | "1d"
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Ticker:
    """Current bid/ask/last for a symbol."""
    symbol: str
    timestamp_utc: str
    bid: float
    ask: float
    last: float
    volume_24h: float
    change_pct_24h: float = 0.0


@dataclass(frozen=True)
class OrderBook:
    """Simplified order book snapshot."""
    symbol: str
    timestamp_utc: str
    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, qty)
    asks: list[tuple[float, float]] = field(default_factory=list)
    spread_pct: float = 0.0


@dataclass(frozen=True)
class MarketDataPoint:
    """Single market data observation used in analysis."""
    symbol: str
    timestamp_utc: str
    price: float
    volume_24h: float
    change_pct_24h: float
    source: str       # "mock" | "binance" | "coinbase" | etc.
    is_stale: bool = False
    freshness_seconds: float = 0.0


@dataclass(frozen=True)
class MarketDataSnapshot:
    """Read-only provider snapshot for operator-facing market data surfaces."""

    symbol: str
    provider: str
    retrieved_at_utc: str
    source_timestamp_utc: str | None
    price: float | None
    is_stale: bool
    freshness_seconds: float | None
    available: bool
    error: str | None = None
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "market_data_snapshot",
            "symbol": self.symbol,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at_utc,
            "source_timestamp": self.source_timestamp_utc,
            "price": self.price,
            "is_stale": self.is_stale,
            "freshness_seconds": self.freshness_seconds,
            "available": self.available,
            "error": self.error,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }
