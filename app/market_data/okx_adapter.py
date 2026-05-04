"""OKX V5 public-REST market data adapter (linear perpetual swap).

Endpoint: GET https://www.okx.com/api/v5/market/ticker?instId=<INST>

OKX uses a different symbol convention than Bybit/Binance:
  KAI canonical  →  OKX instId
  BTC/USDT       →  BTC-USDT-SWAP   (perpetual)
  HYPEUSDT       →  HYPE-USDT-SWAP
The '-SWAP' suffix selects the perpetual contract (vs. dated futures).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.models import OHLCV, Ticker

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://www.okx.com"


def _to_okx_inst_id(raw_symbol: str) -> str:
    """Convert KAI canonical symbol to OKX perpetual swap instId."""
    candidate = raw_symbol.strip().upper()
    if not candidate:
        return ""
    if candidate.endswith("-SWAP"):
        return candidate
    if "/" in candidate:
        base, _, quote = candidate.partition("/")
        return f"{base}-{quote}-SWAP"
    if "-" in candidate:
        base, _, quote = candidate.partition("-")
        return f"{base}-{quote}-SWAP"
    # Plain pair like "BTCUSDT" — peel off a known quote
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if candidate.endswith(quote) and len(candidate) > len(quote):
            return f"{candidate[: -len(quote)]}-{quote}-SWAP"
    return f"{candidate}-USDT-SWAP"


def _from_okx_inst_id(inst_id: str) -> str:
    """OKX 'BTC-USDT-SWAP' → 'BTC/USDT' for canonical reporting."""
    parts = inst_id.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return inst_id


class OKXAdapter(BaseMarketDataAdapter):
    """OKX V5 public ticker (perpetual swap)."""

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
        return "okx"

    async def get_ticker(self, symbol: str) -> Ticker | None:
        inst_id = _to_okx_inst_id(symbol)
        if not inst_id:
            self.last_error = "empty_symbol"
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base}/api/v5/market/ticker",
                    params={"instId": inst_id},
                )
        except (httpx.HTTPError, OSError) as exc:
            self.last_error = f"transport_error:{exc}"
            return None
        if resp.status_code != 200:
            self.last_error = f"http_{resp.status_code}"
            return None
        try:
            data = resp.json()
        except ValueError:
            self.last_error = "json_decode_error"
            return None
        if not isinstance(data, dict):
            self.last_error = "unexpected_payload"
            return None
        # OKX wraps payload in {"code":"0","data":[...]}
        rows = data.get("data") or []
        if not rows or not isinstance(rows[0], dict):
            self.last_error = "symbol_not_found"
            return None
        row = rows[0]
        try:
            last = float(row.get("last", 0.0) or 0.0)
            volume = float(row.get("vol24h", 0.0) or 0.0)
            bid = float(row.get("bidPx", last) or last)
            ask = float(row.get("askPx", last) or last)
            open_24h = float(row.get("open24h", last) or last)
            change_pct = ((last - open_24h) / open_24h * 100.0) if open_24h > 0 else 0.0
        except (TypeError, ValueError):
            self.last_error = "ticker_parse_error"
            return None
        if last <= 0:
            self.last_error = "non_positive_price"
            return None
        ts_ms = row.get("ts")
        try:
            ts_ms_i = int(ts_ms) if ts_ms is not None else 0
        except (TypeError, ValueError):
            ts_ms_i = 0
        if ts_ms_i > 0:
            source_ts = datetime.fromtimestamp(ts_ms_i / 1000, tz=UTC).isoformat()
        else:
            source_ts = datetime.now(UTC).isoformat()
        return Ticker(
            symbol=_from_okx_inst_id(inst_id),
            timestamp_utc=source_ts,
            bid=bid,
            ask=ask,
            last=last,
            volume_24h=volume,
            change_pct_24h=change_pct,
        )

    async def get_price(self, symbol: str) -> float | None:
        ticker = await self.get_ticker(symbol)
        return ticker.last if ticker is not None else None

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        del symbol, timeframe, limit
        return []
