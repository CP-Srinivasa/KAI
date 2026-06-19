"""CoinGecko read-only market data adapter."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import UTC, datetime, timedelta

import httpx

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.coingecko_overview import CoinGeckoMarketOverview
from app.market_data.models import OHLCV, MarketDataPoint, MarketDataSnapshot, Ticker

logger = logging.getLogger(__name__)

_COINGECKO_FREE_BASE = "https://api.coingecko.com/api/v3"
_COINGECKO_PRO_BASE = "https://pro-api.coingecko.com/api/v3"
_DEFAULT_FRESHNESS_SECONDS = 120.0
_DEFAULT_TIMEOUT_SECONDS = 10
_SUPPORTED_QUOTES = frozenset({"USD", "USDT"})

# Backoff profiles. Free-tier is bursty and often throttles for 30-60s;
# paid-tier (pro-api with x-cg-pro-api-key, 250-500 req/min) very rarely 429s
# and recovers in seconds, so the schedule is much tighter.
_BACKOFF_SCHEDULE_FREE = [15.0, 30.0, 60.0]
_BACKOFF_SCHEDULE_PRO = [2.0, 5.0, 10.0]

# Process-wide rolling-window request counter.  Gives us early warning
# before we approach the 250 rpm Pro ceiling — Monitor + Briefing +
# TradingLoop + Alerts all share this budget without any cross-call
# awareness otherwise.  The counter does not throttle; it only observes.
_REQUEST_WINDOW_SECONDS = 60.0
_REQUEST_TIMESTAMPS: deque[float] = deque(maxlen=2000)
_RATE_WARN_THRESHOLD_RPM = 200
_RATE_WARN_MIN_INTERVAL_SECONDS = 60.0
_last_rate_warn_ts: float = 0.0


def _record_request_and_maybe_warn() -> int:
    """Append 'now' to the rolling window, prune old entries, emit a WARN
    when the window count crosses the threshold.  Returns current rpm."""
    global _last_rate_warn_ts
    now = time.monotonic()
    _REQUEST_TIMESTAMPS.append(now)
    cutoff = now - _REQUEST_WINDOW_SECONDS
    while _REQUEST_TIMESTAMPS and _REQUEST_TIMESTAMPS[0] < cutoff:
        _REQUEST_TIMESTAMPS.popleft()
    rpm = len(_REQUEST_TIMESTAMPS)
    if (
        rpm >= _RATE_WARN_THRESHOLD_RPM
        and (now - _last_rate_warn_ts) >= _RATE_WARN_MIN_INTERVAL_SECONDS
    ):
        _last_rate_warn_ts = now
        # Monthly projection at current rate: rpm * 60 min * 24 h * 30 d
        projected_monthly_k = rpm * 60 * 24 * 30 / 1000
        logger.warning(
            "[CoinGecko] rolling rpm=%d crossed warn threshold (%d). "
            "Projected monthly budget at this rate: %.0fk calls",
            rpm,
            _RATE_WARN_THRESHOLD_RPM,
            projected_monthly_k,
        )
    return rpm


_BASE_ASSET_TO_COINGECKO: dict[str, str] = {
    # Majors
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "ADA": "cardano",
    "XRP": "ripple",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    # Polygon rebrand (Sept 2024): MATIC migrated to POL. The legacy
    # matic-network id still resolves on CoinGecko but returns price=None.
    # Both symbols map to the active polygon-ecosystem-token.
    "POL": "polygon-ecosystem-token",
    "MATIC": "polygon-ecosystem-token",
    # V25 (2026-05-04): Symbol-Mapping erweitert um häufige Premium-Channel-
    # Coins. Strukturell besser wäre dynamisches Lookup via CoinGecko
    # /coins/list (10k+ tokens, daily-cached). TODO: V26 — dynamic mapping
    # mit on-demand-refresh + Persistent-Cache, um Hardcoding zu beenden.
    # Bis dahin Whitelist der Top-Premium-Channel-Symbole.
    "HYPE": "hyperliquid",
    "PEPE": "pepe",
    "DOGE": "dogecoin",
    "SHIB": "shiba-inu",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
    "TRX": "tron",
    "TON": "the-open-network",
    "NEAR": "near",
    "APT": "aptos",
    "SUI": "sui",
    "ARB": "arbitrum",
    "OP": "optimism",
    "INJ": "injective-protocol",
    "TIA": "celestia",
    "SEI": "sei-network",
    "JUP": "jupiter-exchange-solana",
    "LTC": "litecoin",
    "DASH": "dash",
    "ATOM": "cosmos",
    "FIL": "filecoin",
    "RNDR": "render-token",
    "RENDER": "render-token",
    "FET": "fetch-ai",
    "ICP": "internet-computer",
    "AAVE": "aave",
    "UNI": "uniswap",
    "CRV": "curve-dao-token",
    "MKR": "maker",
    "GUN": "gun",
    "AKE": "ake",
    "ENSO": "enso-finance",
    "1000LUNC": "terra-luna",
    "LUNC": "terra-luna",
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


def _overview_from_payload(
    payload: dict[str, object], normalized_symbol: str
) -> CoinGeckoMarketOverview:
    """Build a G1 overview record from one ``/coins/markets`` row (None-tolerant)."""
    last_updated = payload.get("last_updated")
    if isinstance(last_updated, str) and last_updated:
        timestamp_utc = last_updated
    else:
        timestamp_utc = datetime.now(UTC).isoformat()
    rank = payload.get("market_cap_rank")
    market_cap = payload.get("market_cap")
    change_30d = payload.get("price_change_percentage_30d_in_currency")
    return CoinGeckoMarketOverview(
        symbol=normalized_symbol,
        timestamp_utc=timestamp_utc,
        market_cap_rank=int(rank) if isinstance(rank, (int, float)) else None,
        market_cap=float(market_cap) if isinstance(market_cap, (int, float)) else None,
        price_change_pct_30d=(float(change_30d) if isinstance(change_30d, (int, float)) else None),
    )


class CoinGeckoAdapter(BaseMarketDataAdapter):
    """Read-only CoinGecko market data adapter with stale detection."""

    def __init__(
        self,
        *,
        freshness_threshold_seconds: float = _DEFAULT_FRESHNESS_SECONDS,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        api_key: str | None = None,
    ) -> None:
        self._freshness_threshold = freshness_threshold_seconds
        self._timeout = timeout_seconds
        self._last_error: str | None = None

        resolved_key = (api_key or "").strip() or None
        self._api_key: str | None = resolved_key
        self._base_url = _COINGECKO_PRO_BASE if resolved_key else _COINGECKO_FREE_BASE
        self._backoff_schedule = _BACKOFF_SCHEDULE_PRO if resolved_key else _BACKOFF_SCHEDULE_FREE

    @property
    def base_url(self) -> str:
        """Expose the active endpoint for tests/observability."""
        return self._base_url

    @property
    def is_pro_tier(self) -> bool:
        return self._api_key is not None

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
            f"{self._base_url}/coins/markets",
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
            f"{self._base_url}/coins/{coin_id}/ohlc",
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
            f"{self._base_url}/coins/{coin_id}/market_chart/range",
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

    async def get_market_overview(self, symbol: str) -> CoinGeckoMarketOverview | None:
        """G1: market-cap rank/value + 30d momentum from ``/coins/markets``.

        Reuses the exact same proven endpoint as ``get_ticker`` (same call,
        same response shape) but surfaces the rank/market-cap/30d fields that
        the price path discards. Read-only; fail-closed to ``None`` on any
        missing/invalid payload. ``None``-tolerant per field (rank is the core
        value; a missing 30d change does not invalidate the record).
        """
        resolved = _resolve_symbol(symbol)
        if resolved is None:
            self._set_error("unsupported_symbol")
            return None
        normalized_symbol, coin_id = resolved

        data = await self._get_json(
            f"{self._base_url}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": coin_id,
                "price_change_percentage": "30d",
            },
        )
        if not isinstance(data, list) or len(data) == 0:
            self._set_error("missing_coin_payload")
            return None
        payload = data[0]
        if not isinstance(payload, dict):
            self._set_error("missing_coin_payload")
            return None

        self._clear_error()
        return _overview_from_payload(payload, normalized_symbol)

    async def get_market_overview_batch(self, symbols: list[str]) -> list[CoinGeckoMarketOverview]:
        """G1 batch: market-cap rank/value + 30d for many symbols in ONE call.

        CoinGecko's ``/coins/markets`` accepts a comma-separated ``ids`` list and
        returns all coins in a single response — so N symbols cost ONE request,
        not N. That is essential on the free tier: N sequential single-symbol
        calls trip the rate limit (429 → 15/30/60s backoff) and blow the
        refresh deadline, writing nothing. Unresolvable symbols are skipped
        silently (fail-safe). Read-only; returns ``[]`` on any non-list payload.
        """
        id_to_symbol: dict[str, str] = {}
        for raw in symbols:
            resolved = _resolve_symbol(raw)
            if resolved is None:
                continue
            normalized_symbol, coin_id = resolved
            id_to_symbol[coin_id] = normalized_symbol
        if not id_to_symbol:
            self._set_error("no_resolvable_symbols")
            return []

        data = await self._get_json(
            f"{self._base_url}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(sorted(id_to_symbol)),
                "price_change_percentage": "30d",
                "per_page": "250",
                "page": "1",
            },
        )
        if not isinstance(data, list):
            self._set_error("missing_coin_payload")
            return []

        out: list[CoinGeckoMarketOverview] = []
        for payload in data:
            if not isinstance(payload, dict):
                continue
            mapped_symbol = id_to_symbol.get(str(payload.get("id")))
            if mapped_symbol is None:
                continue
            out.append(_overview_from_payload(payload, mapped_symbol))
        if out:
            self._clear_error()
        return out

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, object] | list[object] | None:
        # 429-aware retry. Free tier: 15s/30s/60s backoff (bursty, slow to
        # recover). Paid tier: 2s/5s/10s (rare and transient). Retry-After
        # from the server is honoured when present.
        max_attempts = 4
        backoff_schedule = self._backoff_schedule
        headers: dict[str, str] | None = None
        if self._api_key:
            headers = {"x-cg-pro-api-key": self._api_key}

        for attempt in range(max_attempts):
            try:
                _record_request_and_maybe_warn()
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(url, params=params, headers=headers)

                if response.status_code == 200:
                    payload: dict[str, object] | list[object] = response.json()
                    self._clear_error()
                    return payload

                if response.status_code == 429 and attempt < max_attempts - 1:
                    retry_after_header = response.headers.get("Retry-After")
                    try:
                        wait_s = (
                            float(retry_after_header)
                            if retry_after_header
                            else backoff_schedule[attempt]
                        )
                    except (TypeError, ValueError):
                        wait_s = backoff_schedule[attempt]
                    # Cap at 120s to avoid multi-minute stalls.
                    wait_s = min(max(wait_s, 1.0), 120.0)
                    logger.warning(
                        "[CoinGecko] HTTP 429 (attempt %d/%d) — backing off %.1fs for %s",
                        attempt + 1,
                        max_attempts,
                        wait_s,
                        url,
                    )
                    await asyncio.sleep(wait_s)
                    continue

                self._set_error(f"http_{response.status_code}")
                logger.error("[CoinGecko] HTTP %s for %s", response.status_code, url)
                return None

            except httpx.TimeoutException:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(backoff_schedule[attempt])
                    continue
                self._set_error("timeout")
                logger.error("[CoinGecko] Timeout for %s", url)
                return None
            except Exception as exc:  # noqa: BLE001
                self._set_error(f"request_error:{exc}")
                logger.error("[CoinGecko] Request error for %s: %s", url, exc)
                return None

        # Exhausted retries on 429.
        self._set_error("http_429_exhausted")
        logger.error("[CoinGecko] Rate limited (429) — exhausted retries for %s", url)
        return None
