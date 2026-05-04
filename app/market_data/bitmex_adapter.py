"""BitMEX public-REST market data adapter (perpetual futures).

Endpoint: GET https://www.bitmex.com/api/v1/instrument
            ?symbol=<SYM>&columns=lastPrice,timestamp&count=1

Symbol convention quirk: BitMEX uses 'XBT' instead of 'BTC' historically,
so 'BTC/USDT' must be remapped to 'XBTUSDT'. All other tickers keep the
common Base+Quote concatenation. BitMEX has narrower altcoin coverage
than Bybit/Binance Futures so it serves primarily as a redundancy layer
for BTC + the largest tokens.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.models import OHLCV, Ticker

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://www.bitmex.com"

# BitMEX uses XBT instead of BTC. Build a small alias map; everything
# else is taken verbatim from the canonical input.
_BASE_ALIAS_TO_BITMEX: dict[str, str] = {"BTC": "XBT"}


def _to_bitmex_symbol(raw_symbol: str) -> str:
    candidate = raw_symbol.strip().upper()
    if not candidate:
        return ""
    base = ""
    quote = ""
    if "/" in candidate:
        base, _, quote = candidate.partition("/")
    elif "-" in candidate:
        base, _, quote = candidate.partition("-")
    else:
        for q in ("USDT", "USDC", "USD", "BTC", "ETH"):
            if candidate.endswith(q) and len(candidate) > len(q):
                base, quote = candidate[: -len(q)], q
                break
        else:
            return candidate
    base_remapped = _BASE_ALIAS_TO_BITMEX.get(base, base)
    return f"{base_remapped}{quote}"


def _from_bitmex_symbol(sym: str) -> str:
    candidate = sym.strip().upper()
    inverse = {v: k for k, v in _BASE_ALIAS_TO_BITMEX.items()}
    for q in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if candidate.endswith(q) and len(candidate) > len(q):
            base = candidate[: -len(q)]
            quote = q
            base_canonical = inverse.get(base, base)
            return f"{base_canonical}/{quote}"
    return candidate


class BitMEXAdapter(BaseMarketDataAdapter):
    """Read-only BitMEX instrument-ticker source."""

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
        return "bitmex"

    async def get_ticker(self, symbol: str) -> Ticker | None:
        sym = _to_bitmex_symbol(symbol)
        if not sym:
            self.last_error = "empty_symbol"
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base}/api/v1/instrument",
                    params={
                        "symbol": sym,
                        "columns": "lastPrice,timestamp,bidPrice,askPrice,volume24h",
                        "count": "1",
                    },
                )
        except (httpx.HTTPError, OSError) as exc:
            self.last_error = f"transport_error:{exc}"
            return None
        if resp.status_code in (429, 503):
            self.last_error = "rate_limited"
            return None
        if resp.status_code != 200:
            self.last_error = f"http_{resp.status_code}"
            return None
        try:
            data = resp.json()
        except ValueError:
            self.last_error = "json_decode_error"
            return None
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            self.last_error = "symbol_not_found"
            return None
        row = data[0]
        last_raw = row.get("lastPrice")
        if last_raw is None:
            self.last_error = "no_last_price"
            return None
        try:
            last = float(last_raw)
            bid = float(row.get("bidPrice", last) or last)
            ask = float(row.get("askPrice", last) or last)
            volume_24h = float(row.get("volume24h", 0.0) or 0.0)
        except (TypeError, ValueError):
            self.last_error = "ticker_parse_error"
            return None
        if last <= 0:
            self.last_error = "non_positive_price"
            return None
        ts_iso_raw = row.get("timestamp")
        if isinstance(ts_iso_raw, str) and ts_iso_raw:
            source_ts = ts_iso_raw
        else:
            source_ts = datetime.now(UTC).isoformat()
        return Ticker(
            symbol=_from_bitmex_symbol(sym),
            timestamp_utc=source_ts,
            bid=bid,
            ask=ask,
            last=last,
            volume_24h=volume_24h,
            change_pct_24h=0.0,
        )

    async def get_price(self, symbol: str) -> float | None:
        ticker = await self.get_ticker(symbol)
        return ticker.last if ticker is not None else None

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        del symbol, timeframe, limit
        return []
