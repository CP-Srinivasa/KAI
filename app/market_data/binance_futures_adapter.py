"""Binance USD-M Futures public-REST market data adapter (V25-D).

Endpoint: GET https://fapi.binance.com/fapi/v1/ticker/24hr

Why a separate Binance-Futures adapter (vs. the existing spot adapter in
binance_adapter.py): the premium Telegram channel posts Bybit-Futures pairs
with leveraged tickers (1000LUNCUSDT, etc.) that Binance Spot does not
list — but Binance Futures does, with a 1:1 symbol convention to Bybit
Linear. This adapter is the second-tier provider after Bybit in the
fallback cascade so any signal that survives Bybit-resolve still has a
matching reference price on Binance.

Symbol convention: KAI canonical 'BTC/USDT' → 'BTCUSDT'.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.models import OHLCV, Ticker

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://fapi.binance.com"


def _normalize_symbol(raw_symbol: str) -> str:
    candidate = raw_symbol.strip().upper()
    if not candidate:
        return ""
    for sep in ("/", "-", ":"):
        if sep in candidate:
            base, _, quote = candidate.partition(sep)
            return f"{base}{quote}"
    return candidate


def _canonical_symbol(raw_symbol: str) -> str:
    candidate = raw_symbol.strip().upper()
    if "/" in candidate:
        return candidate
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH"):
        if candidate.endswith(quote) and len(candidate) > len(quote):
            return f"{candidate[: -len(quote)]}/{quote}"
    return candidate


class BinanceFuturesAdapter(BaseMarketDataAdapter):
    """Read-only Binance USD-M futures price source."""

    def __init__(
        self,
        *,
        freshness_threshold_seconds: float = 120.0,
        timeout_seconds: int = 10,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._freshness = freshness_threshold_seconds
        self._timeout = timeout_seconds
        self._base = base_url
        self.last_error: str | None = None

    @property
    def adapter_name(self) -> str:
        return "binance_futures"

    async def get_ticker(self, symbol: str) -> Ticker | None:
        sym = _normalize_symbol(symbol)
        if not sym:
            self.last_error = "empty_symbol"
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base}/fapi/v1/ticker/24hr",
                    params={"symbol": sym},
                )
        except (httpx.HTTPError, OSError) as exc:
            self.last_error = f"transport_error:{exc}"
            return None
        if resp.status_code in (400, 404):
            # Binance returns 400 with code -1121 for invalid symbol
            self.last_error = "symbol_not_found"
            return None
        if resp.status_code in (418, 429):
            self.last_error = "rate_limited"
            return None
        if resp.status_code != 200:
            self.last_error = f"http_{resp.status_code}"
            return None
        try:
            row = resp.json()
        except ValueError:
            self.last_error = "json_decode_error"
            return None
        if not isinstance(row, dict) or "lastPrice" not in row:
            self.last_error = "unexpected_payload"
            return None
        try:
            last = float(row.get("lastPrice", 0.0) or 0.0)
            volume = float(row.get("volume", 0.0) or 0.0)
            change = float(row.get("priceChangePercent", 0.0) or 0.0)
            bid = float(row.get("bidPrice", last) or last)
            ask = float(row.get("askPrice", last) or last)
        except (TypeError, ValueError):
            self.last_error = "ticker_parse_error"
            return None
        if last <= 0:
            self.last_error = "non_positive_price"
            return None
        ts_ms = row.get("closeTime")
        if isinstance(ts_ms, (int, float)) and ts_ms > 0:
            source_ts = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC).isoformat()
        else:
            source_ts = datetime.now(UTC).isoformat()
        return Ticker(
            symbol=_canonical_symbol(sym),
            timestamp_utc=source_ts,
            bid=bid,
            ask=ask,
            last=last,
            volume_24h=volume,
            change_pct_24h=change,
        )

    async def get_price(self, symbol: str) -> float | None:
        ticker = await self.get_ticker(symbol)
        return ticker.last if ticker is not None else None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[OHLCV]:
        del symbol, timeframe, limit
        return []
