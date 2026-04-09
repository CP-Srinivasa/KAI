"""CoinGecko read-only market data adapter."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import httpx

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.models import OHLCV, MarketDataPoint, MarketDataSnapshot, Ticker

logger = logging.getLogger(__name__)

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_DEFAULT_FRESHNESS_SECONDS = 120.0
_DEFAULT_TIMEOUT_SECONDS = 10
_SUPPORTED_QUOTES = frozenset({"USD", "USDT"})

_BASE_ASSET_TO_COINGECKO: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "ADA": "cardano",
    "XRP": "ripple",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "LINK": "chainlink",
}


def _normalize_symbol(raw_symbol: str) -> str:
    candidate = raw_symbol.strip().upper()
    if not candidate:
        return candidate
    if "/" in candidate:
        base, quote = candidate.split("/", 1)
        return f"{base}/{quote}"
    if "-" in candidate:
        base, quote = candidate.split("-", 1)
        return f"{base}/{quote}"
    return f"{candidate}/USDT"


def _resolve_symbol(symbol: str) -> tuple[str, str] | None:
    """Resolve raw symbol to canonical symbol + CoinGecko id."""
    normalized = _normalize_symbol(symbol)
    if "/" not in normalized:
        return None
    base, quote = normalized.split("/", 1)
    if quote not in _SUPPORTED_QUOTES:
        return None
    coin_id = _BASE_ASSET_TO_COINGECKO.get(base)
    if coin_id is None:
        return None
    return normalized, coin_id


def _resolve_coingecko_id(symbol: str) -> str | None:
    """Backward-compatible helper used by tests/contracts."""
    resolved = _resolve_symbol(symbol)
    if resolved is None:
        return None
    _normalized, coin_id = resolved
    return coin_id


def _timeframe_to_days(timeframe: str, limit: int) -> int:
    minutes_map = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }
    minutes = minutes_map.get(timeframe, 60)
    total_minutes = max(minutes * limit, 1)
    days = max(1, total_minutes // 1440)
    return min(days, 365)


def _to_unix_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _nearest_price(
    prices: list[tuple[int, float]],
    target_unix_seconds: int,
    *,
    max_gap_seconds: int,
) -> float | None:
    if not prices:
        return None

    best_ts, best_price = min(
        prices,
        key=lambda row: abs(row[0] - target_unix_seconds),
    )
    if abs(best_ts - target_unix_seconds) > max_gap_seconds:
        return None
    return best_price


class CoinGeckoAdapter(BaseMarketDataAdapter):
    """Read-only CoinGecko market data adapter with stale detection."""

    def __init__(
        self,
        *,
        freshness_threshold_seconds: float = _DEFAULT_FRESHNESS_SECONDS,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._freshness_threshold = freshness_threshold_seconds
        self._timeout = timeout_seconds
        self._last_error: str | None = None

    @property
    def adapter_name(self) -> str:
        return "coingecko"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _set_error(self, message: str) -> None:
        self._last_error = message

    def _clear_error(self) -> None:
        self._last_error = None

    async def get_price(self, symbol: str) -> float | None:
        ticker = await self.get_ticker(symbol)
        if ticker is None:
            return None
        return ticker.last

    async def get_ticker(self, symbol: str) -> Ticker | None:
        resolved = _resolve_symbol(symbol)
        if resolved is None:
            self._set_error("unsupported_symbol")
            return None
        normalized_symbol, coin_id = resolved

        # D-120: Use /coins/markets instead of /simple/price to get both
        # 24h and 7d price change in a single API call.
        data = await self._get_json(
            f"{_COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": coin_id,
                "price_change_percentage": "7d",
            },
        )
        if not isinstance(data, list) or len(data) == 0:
            self._set_error("missing_coin_payload")
            return None

        coin_payload = data[0]
        if not isinstance(coin_payload, dict):
            self._set_error("missing_coin_payload")
            return None

        price = coin_payload.get("current_price")
        if not isinstance(price, (int, float)) or price <= 0:
            self._set_error("missing_or_invalid_price")
            return None

        last_updated = coin_payload.get("last_updated")
        if isinstance(last_updated, str) and last_updated:
            timestamp_utc = last_updated
        else:
            timestamp_utc = datetime.now(UTC).isoformat()

        volume = coin_payload.get("total_volume")
        change_24h = coin_payload.get("price_change_percentage_24h")
        change_7d = coin_payload.get("price_change_percentage_7d_in_currency")

        self._clear_error()
        return Ticker(
            symbol=normalized_symbol,
            timestamp_utc=timestamp_utc,
            bid=float(price),
            ask=float(price),
            last=float(price),
            volume_24h=float(volume) if isinstance(volume, (int, float)) else 0.0,
            change_pct_24h=float(change_24h) if isinstance(change_24h, (int, float)) else 0.0,
            change_pct_7d=float(change_7d) if isinstance(change_7d, (int, float)) else 0.0,
        )

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> list[OHLCV]:
        resolved = _resolve_symbol(symbol)
        if resolved is None:
            self._set_error("unsupported_symbol")
            return []
        normalized_symbol, coin_id = resolved
        days = _timeframe_to_days(timeframe, limit)

        data = await self._get_json(
            f"{_COINGECKO_BASE}/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": str(days)},
        )
        if not isinstance(data, list):
            self._set_error("invalid_ohlcv_payload")
            return []

        candles: list[OHLCV] = []
        for row in data[-limit:]:
            if not isinstance(row, list) or len(row) < 5:
                continue
            ts_ms, open_price, high_price, low_price, close_price = row[:5]
            if not isinstance(ts_ms, (int, float)):
                continue
            if not all(
                isinstance(value, (int, float))
                for value in [open_price, high_price, low_price, close_price]
            ):
                continue
            candles.append(
                OHLCV(
                    symbol=normalized_symbol,
                    timestamp_utc=datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat(),
                    timeframe=timeframe,
                    open=float(open_price),
                    high=float(high_price),
                    low=float(low_price),
                    close=float(close_price),
                    volume=0.0,
                )
            )

        if not candles:
            self._set_error("empty_ohlcv")
        else:
            self._clear_error()
        return candles

    async def get_price_change_between(
        self,
        symbol: str,
        *,
        start_utc: datetime,
        end_utc: datetime,
        max_point_gap_seconds: int = 3 * 3600,
        padding_seconds: int = 2 * 3600,
    ) -> tuple[float, float, float] | None:
        """Resolve historical move between two timestamps via nearest sampled prices.

        Returns:
            (price_at_start, price_at_end, move_pct) or None when unavailable.
        """
        if end_utc <= start_utc:
            self._set_error("invalid_time_range")
            return None

        resolved = _resolve_symbol(symbol)
        if resolved is None:
            self._set_error("unsupported_symbol")
            return None
        _normalized_symbol, coin_id = resolved

        query_from = _to_unix_seconds(start_utc - timedelta(seconds=padding_seconds))
        query_to = _to_unix_seconds(end_utc + timedelta(seconds=padding_seconds))
        if query_to <= query_from:
            self._set_error("invalid_time_range")
            return None

        data = await self._get_json(
            f"{_COINGECKO_BASE}/coins/{coin_id}/market_chart/range",
            params={
                "vs_currency": "usd",
                "from": str(query_from),
                "to": str(query_to),
            },
        )
        if not isinstance(data, dict):
            self._set_error("invalid_range_payload")
            return None

        raw_prices = data.get("prices")
        if not isinstance(raw_prices, list):
            self._set_error("missing_range_prices")
            return None

        points: list[tuple[int, float]] = []
        for row in raw_prices:
            if not isinstance(row, list) or len(row) < 2:
                continue
            ts_ms, price = row[0], row[1]
            if not isinstance(ts_ms, (int, float)):
                continue
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            points.append((int(ts_ms / 1000), float(price)))

        if not points:
            self._set_error("empty_range_prices")
            return None

        start_price = _nearest_price(
            points,
            _to_unix_seconds(start_utc),
            max_gap_seconds=max_point_gap_seconds,
        )
        end_price = _nearest_price(
            points,
            _to_unix_seconds(end_utc),
            max_gap_seconds=max_point_gap_seconds,
        )
        if start_price is None or end_price is None:
            self._set_error("missing_nearby_price_point")
            return None

        move_pct = (end_price - start_price) / start_price * 100.0
        self._clear_error()
        return (start_price, end_price, round(move_pct, 4))

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
        snapshot = await self.get_market_data_snapshot(symbol)
        if (
            not snapshot.available
            or snapshot.price is None
            or snapshot.source_timestamp_utc is None
        ):
            return None
        return MarketDataPoint(
            symbol=snapshot.symbol,
            timestamp_utc=snapshot.source_timestamp_utc,
            price=snapshot.price,
            volume_24h=0.0,
            change_pct_24h=0.0,
            source=self.adapter_name,
            is_stale=snapshot.is_stale,
            freshness_seconds=snapshot.freshness_seconds or 0.0,
        )

    async def get_market_data_snapshot(self, symbol: str) -> MarketDataSnapshot:
        retrieved_at = datetime.now(UTC).isoformat()
        ticker = await self.get_ticker(symbol)
        if ticker is None:
            return MarketDataSnapshot(
                symbol=_normalize_symbol(symbol),
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
            freshness_seconds=(round(age_seconds, 2) if age_seconds is not None else None),
            available=True,
            error=("stale_data" if is_stale else None),
        )

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, object] | list[object] | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
            if response.status_code != 200:
                self._set_error(f"http_{response.status_code}")
                logger.error("[CoinGecko] HTTP %s for %s", response.status_code, url)
                return None
            payload: dict[str, object] | list[object] = response.json()
            self._clear_error()
            return payload
        except httpx.TimeoutException:
            self._set_error("timeout")
            logger.error("[CoinGecko] Timeout for %s", url)
            return None
        except Exception as exc:  # noqa: BLE001
            self._set_error(f"request_error:{exc}")
            logger.error("[CoinGecko] Request error for %s: %s", url, exc)
            return None
