"""Public REST candle fetchers for the close-evidence shadow path.

These adapters have no credentials and no order surface.  They translate two
provider payloads into the collector's :class:`VenueCandle` boundary and raise
a named error on every transport or schema ambiguity; the collector then emits
``FETCH_FAILED`` instead of mistaking corrupt data for an empty market window.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.execution.close_evidence import VenueCandle

_INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}
_BYBIT_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "1h": "60"}


class CandleFetchError(RuntimeError):
    """A provider response cannot serve as close evidence."""


def _pair(symbol: str) -> str:
    candidate = symbol.strip().upper()
    parts = re.split(r"[/:-]", candidate)
    if len(parts) == 2 and all(re.fullmatch(r"[A-Z0-9]{2,12}", part) for part in parts):
        return "".join(parts)
    if re.fullmatch(r"[A-Z0-9]{5,24}", candidate):
        return candidate
    raise CandleFetchError(f"invalid symbol: {symbol!r}")


def _request_limit(interval: str, start_ms: int, end_ms: int) -> int:
    width = _INTERVAL_MS.get(interval)
    if width is None:
        raise CandleFetchError(f"invalid interval: {interval!r}")
    if isinstance(start_ms, bool) or isinstance(end_ms, bool) or start_ms < 0 or end_ms <= start_ms:
        raise CandleFetchError("invalid candle window")
    limit = math.ceil((end_ms - start_ms) / width)
    if limit > 1000:
        raise CandleFetchError(f"invalid candle window: {limit} rows exceeds provider limit")
    return limit


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("not numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("not finite and positive")
    return parsed


def _parse_rows(rows: object, *, venue: str) -> list[VenueCandle]:
    if not isinstance(rows, list):
        raise CandleFetchError(f"{venue} invalid payload: candle list missing")
    candles: list[VenueCandle] = []
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, list) or len(row) < 5 or isinstance(row[0], bool):
                raise ValueError("row shape")
            opened = int(row[0])
            open_price, high, low, close = (_number(row[i]) for i in range(1, 5))
            if opened < 0 or low > min(open_price, close) or high < max(open_price, close):
                raise ValueError("OHLC consistency")
        except (TypeError, ValueError, OverflowError) as exc:
            raise CandleFetchError(f"{venue} invalid candle row {index}: {exc}") from exc
        candles.append(VenueCandle(opened, open_price, high, low, close))
    return sorted(candles, key=lambda candle: candle.open_time_ms)


@dataclass(frozen=True)
class _PublicRestFetcher:
    venue: str
    base_url: str
    transport: httpx.BaseTransport | None = None
    timeout_seconds: float = 10.0

    def _get(self, path: str, params: dict[str, Any]) -> object:
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.get(path, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise CandleFetchError(f"{self.venue} HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise CandleFetchError(f"{self.venue} transport/payload error: {exc}") from exc

    def _common(
        self, *, symbol: str, venue: str, interval: str, start_ms: int, end_ms: int
    ) -> tuple[str, int]:
        if venue.strip().lower() != self.venue:
            raise CandleFetchError(f"invalid venue {venue!r}; expected {self.venue!r}")
        return _pair(symbol), _request_limit(interval, start_ms, end_ms)


class BinanceCandleFetcher(_PublicRestFetcher):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("binance", "https://api.binance.com", **kwargs)

    def __call__(
        self, *, symbol: str, venue: str, interval: str, start_ms: int, end_ms: int
    ) -> list[VenueCandle]:
        pair, limit = self._common(
            symbol=symbol, venue=venue, interval=interval, start_ms=start_ms, end_ms=end_ms
        )
        payload = self._get(
            "/api/v3/klines",
            {
                "symbol": pair,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms - 1,
                "limit": limit,
            },
        )
        return _parse_rows(payload, venue=self.venue)


class BybitCandleFetcher(_PublicRestFetcher):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("bybit", "https://api.bybit.com", **kwargs)

    def __call__(
        self, *, symbol: str, venue: str, interval: str, start_ms: int, end_ms: int
    ) -> list[VenueCandle]:
        pair, limit = self._common(
            symbol=symbol, venue=venue, interval=interval, start_ms=start_ms, end_ms=end_ms
        )
        payload = self._get(
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": pair,
                "interval": _BYBIT_INTERVAL[interval],
                "start": start_ms,
                "end": end_ms - 1,
                "limit": limit,
            },
        )
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            code = payload.get("retCode") if isinstance(payload, dict) else "missing"
            message = payload.get("retMsg") if isinstance(payload, dict) else "not an object"
            raise CandleFetchError(f"bybit retCode={code}: {message}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise CandleFetchError("bybit invalid payload: result missing")
        return _parse_rows(result.get("list"), venue=self.venue)
