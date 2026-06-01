"""Bybit V5 public-REST market data adapter (linear perpetuals).

Endpoints used:
    GET /v5/market/tickers?category=linear&symbol=<SYM>

Why Bybit-Linear is the right primary source for the premium-channel signal
flow: the Telegram channel "Bitmex/Bybit/Futures/OKX Premium Signals" posts
Bybit Futures pairs verbatim — including exotic tokens (SWARMS, GIGGLE,
1000LUNC) that CoinGecko's spot-aggregation does not list and Binance Spot
does not have. Bybit's V5 linear endpoint covers these natively.

Symbol convention: KAI canonical "BTC/USDT" → Bybit "BTCUSDT".

Safety invariants (mirror CoinGeckoAdapter / BinanceAdapter):
    - Never raises on transport / parse errors → returns None.
    - 429 (rate-limit) and 403 are surfaced via `last_error` for downstream
      observability.
    - No auth required for public ticker; key would only enable higher rate
      limits which we do not need at our cadence (1 req per pending bridge
      tick, 1×/min default).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.models import OHLCV, MarketDataSnapshot, Ticker

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.bybit.com"


def _normalize_symbol(raw_symbol: str) -> str:
    """KAI canonical → Bybit pair string. 'BTC/USDT' → 'BTCUSDT'."""
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
    for quote in ("USDT", "USDC", "BUSD", "USD", "EUR", "BTC", "ETH"):
        if candidate.endswith(quote) and len(candidate) > len(quote):
            return f"{candidate[: -len(quote)]}/{quote}"
    return candidate


class BybitAdapter(BaseMarketDataAdapter):
    """Bybit V5 read-only adapter (linear perpetual futures)."""

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
        return "bybit"

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params)
        except (httpx.HTTPError, OSError) as exc:
            self.last_error = f"transport_error:{exc}"
            logger.warning("[bybit] %s transport error: %s", path, exc)
            return None
        if resp.status_code == 429:
            self.last_error = "rate_limited"
            logger.warning("[bybit] rate-limited on %s", path)
            return None
        if resp.status_code != 200:
            self.last_error = f"http_{resp.status_code}"
            logger.warning("[bybit] %s status=%s body=%s", path, resp.status_code, resp.text[:160])
            return None
        try:
            data = resp.json()
        except ValueError:
            self.last_error = "json_decode_error"
            return None
        if not isinstance(data, dict):
            self.last_error = "unexpected_payload_type"
            return None
        ret_code = data.get("retCode")
        if ret_code != 0:
            self.last_error = f"api_error:{ret_code}:{data.get('retMsg')}"
            return None
        return data

    async def get_ticker(self, symbol: str) -> Ticker | None:
        bybit_sym = _normalize_symbol(symbol)
        if not bybit_sym:
            self.last_error = "empty_symbol"
            return None
        data = await self._get(
            "/v5/market/tickers",
            {"category": "linear", "symbol": bybit_sym},
        )
        if data is None:
            return None
        result = data.get("result") or {}
        rows = result.get("list") or []
        if not rows or not isinstance(rows[0], dict):
            self.last_error = "symbol_not_found"
            return None
        row = rows[0]
        try:
            last = float(row.get("lastPrice", 0.0) or 0.0)
            volume_24h = float(row.get("volume24h", 0.0) or 0.0)
            change_pct = float(row.get("price24hPcnt", 0.0) or 0.0) * 100.0
            bid = float(row.get("bid1Price", last) or last)
            ask = float(row.get("ask1Price", last) or last)
        except (TypeError, ValueError):
            self.last_error = "ticker_value_parse_error"
            return None
        if last <= 0:
            self.last_error = "non_positive_last_price"
            return None
        ts_ms = data.get("time")
        if isinstance(ts_ms, (int, float)) and ts_ms > 0:
            source_ts = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC).isoformat()
        else:
            source_ts = datetime.now(UTC).isoformat()
        return Ticker(
            symbol=_canonical_symbol(bybit_sym),
            timestamp_utc=source_ts,
            bid=bid,
            ask=ask,
            last=last,
            volume_24h=volume_24h,
            change_pct_24h=change_pct,
        )

    async def get_price(self, symbol: str) -> float | None:
        ticker = await self.get_ticker(symbol)
        return ticker.last if ticker is not None else None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[OHLCV]:
        # OHLCV not required by the bridge; deliberate no-op so we keep
        # the adapter surface minimal until a consumer actually needs it.
        del symbol, timeframe, limit
        return []

    async def get_market_data_snapshot(self, symbol: str) -> MarketDataSnapshot:
        # Override default to surface bybit's last_error in the error field
        # when it set one but get_market_data_point returned None.
        retrieved_at = datetime.now(UTC).isoformat()
        point = await self.get_market_data_point(symbol)
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
                error=self.last_error or "market_data_unavailable",
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
