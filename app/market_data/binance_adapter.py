"""Binance public-REST market data adapter — TV-2 (read-only, no auth).

Endpoints used:
    GET /api/v3/klines        — OHLCV candles
    GET /api/v3/ticker/24hr   — 24h ticker (price, volume, change%)

Symbol convention: KAI canonical "BTC/USDT" → Binance "BTCUSDT".
Timeframe map: 1m, 5m, 15m, 1h, 4h, 1d (Binance native interval strings).

Safety invariants:
    - Never raises on transport/parse errors — returns None / [] and sets
      `last_error` for downstream observability (matches CoinGeckoAdapter).
    - 429 (rate-limit) and 418 (IP ban) trigger backoff retry.
    - Adapter is constructed only when `BINANCE_ENABLED=true` (gated by
      BinanceMarketDataSettings).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

import httpx

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.models import OHLCV, MarketDataPoint, MarketDataSnapshot, Ticker

logger = logging.getLogger(__name__)

_TIMEFRAME_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}
_DEFAULT_BASE_URL = "https://api.binance.com"


def _normalize_symbol(raw_symbol: str) -> str:
    """KAI canonical → Binance pair string. 'BTC/USDT' → 'BTCUSDT'."""
    candidate = raw_symbol.strip().upper()
    if not candidate:
        return ""
    for sep in ("/", "-", ":"):
        if sep in candidate:
            base, _, quote = candidate.partition(sep)
            return f"{base}{quote}"
    return candidate  # already in pair form, e.g. "BTCUSDT"


def _canonical_symbol(raw_symbol: str) -> str:
    """Inverse of _normalize_symbol for output reporting. Best-effort."""
    candidate = raw_symbol.strip().upper()
    if "/" in candidate:
        return candidate
    # Heuristic: split on common quote suffixes.
    for quote in ("USDT", "USDC", "BUSD", "USD", "EUR", "BTC", "ETH"):
        if candidate.endswith(quote) and len(candidate) > len(quote):
            base = candidate[: -len(quote)]
            return f"{base}/{quote}"
    return candidate


class BinanceAdapter(BaseMarketDataAdapter):
    """Read-only Binance public REST adapter.

    Constructed via BinanceMarketDataSettings; never connects until first call.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: int = 10,
        max_retries: int = 3,
        freshness_threshold_seconds: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._freshness_threshold = freshness_threshold_seconds
        self._last_error: str | None = None

    @property
    def adapter_name(self) -> str:
        return "binance"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _set_error(self, message: str) -> None:
        self._last_error = message

    def _clear_error(self) -> None:
        self._last_error = None

    async def get_price(self, symbol: str) -> float | None:
        ticker = await self.get_ticker(symbol)
        return ticker.last if ticker is not None else None

    async def get_ticker(self, symbol: str) -> Ticker | None:
        pair = _normalize_symbol(symbol)
        if not pair:
            self._set_error("empty_symbol")
            return None

        data = await self._get_json(
            f"{self._base_url}/api/v3/ticker/24hr",
            params={"symbol": pair},
        )
        if not isinstance(data, dict):
            self._set_error(self._last_error or "missing_ticker_payload")
            return None

        try:
            last = float(data["lastPrice"])
            bid = float(data.get("bidPrice", last))
            ask = float(data.get("askPrice", last))
            volume = float(data.get("volume", 0.0))
            change_pct = float(data.get("priceChangePercent", 0.0))
        except (KeyError, TypeError, ValueError):
            self._set_error("invalid_ticker_payload")
            return None

        if last <= 0:
            self._set_error("invalid_price")
            return None

        close_time_ms = data.get("closeTime")
        if isinstance(close_time_ms, (int, float)) and close_time_ms > 0:
            timestamp_utc = datetime.fromtimestamp(
                close_time_ms / 1000, tz=UTC
            ).isoformat()
        else:
            timestamp_utc = datetime.now(UTC).isoformat()

        self._clear_error()
        return Ticker(
            symbol=_canonical_symbol(pair),
            timestamp_utc=timestamp_utc,
            bid=bid,
            ask=ask,
            last=last,
            volume_24h=volume,
            change_pct_24h=change_pct,
        )

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> list[OHLCV]:
        pair = _normalize_symbol(symbol)
        if not pair:
            self._set_error("empty_symbol")
            return []
        interval = _TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            self._set_error("unsupported_timeframe")
            return []
        # Binance hard-cap: 1000 candles per request.
        capped_limit = max(1, min(limit, 1000))

        data = await self._get_json(
            f"{self._base_url}/api/v3/klines",
            params={
                "symbol": pair,
                "interval": interval,
                "limit": str(capped_limit),
            },
        )
        if not isinstance(data, list):
            self._set_error(self._last_error or "invalid_klines_payload")
            return []

        canonical = _canonical_symbol(pair)
        candles: list[OHLCV] = []
        for row in data:
            # Binance kline row: [open_time, open, high, low, close, volume,
            # close_time, quote_volume, num_trades, ...] — first 7 always present.
            if not isinstance(row, list) or len(row) < 7:
                continue
            try:
                open_time_ms = int(row[0])
                open_p = float(row[1])
                high_p = float(row[2])
                low_p = float(row[3])
                close_p = float(row[4])
                volume = float(row[5])
            except (TypeError, ValueError):
                continue
            if open_p <= 0 or close_p <= 0:
                continue
            candles.append(
                OHLCV(
                    symbol=canonical,
                    timestamp_utc=datetime.fromtimestamp(
                        open_time_ms / 1000, tz=UTC
                    ).isoformat(),
                    timeframe=timeframe,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=volume,
                )
            )
        if candles:
            self._clear_error()
        else:
            self._set_error("empty_klines")
        return candles

    async def get_market_data_snapshot(self, symbol: str) -> MarketDataSnapshot:
        retrieved_at = datetime.now(UTC).isoformat()
        ticker = await self.get_ticker(symbol)
        if ticker is None:
            return MarketDataSnapshot(
                symbol=_canonical_symbol(_normalize_symbol(symbol)),
                provider=self.adapter_name,
                retrieved_at_utc=retrieved_at,
                source_timestamp_utc=None,
                price=None,
                is_stale=True,
                freshness_seconds=None,
                available=False,
                error=self._last_error or "market_data_unavailable",
            )

        age_seconds: float | None = None
        is_stale = True
        try:
            source_dt = datetime.fromisoformat(ticker.timestamp_utc)
            age_seconds = max(0.0, time.time() - source_dt.timestamp())
            is_stale = age_seconds > self._freshness_threshold
        except ValueError:
            self._set_error("invalid_source_timestamp")

        return MarketDataSnapshot(
            symbol=ticker.symbol,
            provider=self.adapter_name,
            retrieved_at_utc=retrieved_at,
            source_timestamp_utc=ticker.timestamp_utc,
            price=ticker.last,
            is_stale=is_stale,
            freshness_seconds=(
                round(age_seconds, 2) if age_seconds is not None else None
            ),
            available=True,
            error=("stale_data" if is_stale else None),
        )

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
        )

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, object] | list[object] | None:
        backoff_schedule = [2.0, 5.0, 15.0]
        for attempt in range(self._max_retries):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(url, params=params)

                if response.status_code == 200:
                    self._clear_error()
                    payload: dict[str, object] | list[object] = response.json()
                    return payload

                # Binance: 429 = rate limit, 418 = IP ban. Both honor Retry-After.
                if response.status_code in (418, 429) and attempt < self._max_retries - 1:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait_s = (
                            float(retry_after)
                            if retry_after
                            else backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                        )
                    except (TypeError, ValueError):
                        wait_s = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                    wait_s = min(max(wait_s, 1.0), 120.0)
                    logger.warning(
                        "[Binance] HTTP %s (attempt %d/%d) — backing off %.1fs for %s",
                        response.status_code,
                        attempt + 1,
                        self._max_retries,
                        wait_s,
                        url,
                    )
                    await asyncio.sleep(wait_s)
                    continue

                self._set_error(f"http_{response.status_code}")
                logger.error("[Binance] HTTP %s for %s", response.status_code, url)
                return None

            except httpx.TimeoutException:
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(
                        backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                    )
                    continue
                self._set_error("timeout")
                logger.error("[Binance] Timeout for %s", url)
                return None
            except Exception as exc:  # noqa: BLE001
                self._set_error(f"request_error:{exc}")
                logger.error("[Binance] Request error for %s: %s", url, exc)
                return None

        self._set_error("retries_exhausted")
        return None
