"""TradingView datafeed — UNOFFICIAL, isolated, default-off (Track 2, 2026-06-15).

⚠️  RISK / ToS NOTICE (operator-accepted 2026-06-15):
    This hits TradingView's public scanner endpoint (``scanner.tradingview.com``)
    programmatically. That is **reverse-engineered and against TradingView's ToS**
    (automated data extraction). It is included on the operator's explicit, risk-
    accepted decision. To minimise blast radius this module:
      - uses the PUBLIC, UNAUTHENTICATED endpoint → no TV login, so there is no
        account to ban (much lower risk than authenticated scraping),
      - adds NO third-party scraper dependency (httpx only, which we already use),
      - is a STANDALONE class — NOT a ``BaseMarketDataAdapter`` subclass — so it
        can never be wired into the sanctioned market-data fallback chain by
        accident,
      - is DEFAULT-OFF (``TRADINGVIEW_DATAFEED_ENABLED``) and FAIL-SOFT: any
        transport/parse/schema error returns ``[]`` and never raises into a caller.

    The sanctioned exchange-data path (WP-F ``top_symbols_by_volume``) remains the
    primary, ToS-clean source. This is a supplementary, opt-in evidence source for
    TradingView's proprietary signals (notably ``Recommend.All``, the aggregate
    technical rating in [-1, 1]).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_BASE_URL = "https://scanner.tradingview.com"
_DEFAULT_EXCHANGE = "BYBIT"
# TradingView Recommend.All aggregate rating thresholds (TV's own convention).
RATING_STRONG_BUY = 0.5
RATING_BUY = 0.1


@dataclass(frozen=True)
class TradingViewRow:
    """One scanner row, normalised. ``rating`` is Recommend.All in [-1, 1]."""

    symbol: str  # canonical BASE/USDT
    raw_symbol: str  # exchange ticker, e.g. BTCUSDT
    close: float | None
    change_pct: float | None
    rating: float | None  # Recommend.All; >0.5 strong-buy, <-0.5 strong-sell


def _canonical(raw: str) -> str | None:
    """``BYBIT:BTCUSDT`` / ``BTCUSDT`` → ``BTC/USDT`` (USDT pairs only)."""
    ticker = raw.split(":", 1)[-1].strip().upper()
    if ticker.endswith("USDT") and len(ticker) > 4:
        return f"{ticker[:-4]}/USDT"
    return None


class TradingViewDatafeed:
    """Read-only client for the public TradingView crypto scanner. Fail-soft."""

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        exchange: str = _DEFAULT_EXCHANGE,
        timeout_seconds: int = 10,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._exchange = exchange
        self._timeout = timeout_seconds

    async def _scan(
        self, columns: list[str], *, limit: int, sort_by: str
    ) -> list[dict[str, object]]:
        body: dict[str, object] = {
            "filter": [{"left": "exchange", "operation": "in_range", "right": [self._exchange]}],
            "columns": columns,
            "sort": {"sortBy": sort_by, "sortOrder": "desc"},
            "range": [0, max(1, limit)],
        }
        url = f"{self._base}/crypto/scan"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body)
            if resp.status_code != 200:
                logger.warning("tradingview_datafeed.http_error", status=resp.status_code)
                return []
            data = resp.json()
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.warning("tradingview_datafeed.transport_error", error=str(exc)[:200])
            return []
        rows = data.get("data") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else []

    async def top_rows(self, *, limit: int = 50) -> list[TradingViewRow]:
        """Top-``limit`` USDT pairs by volume with close/change/rating. Fail-soft."""
        columns = ["name", "close", "change", "Recommend.All", "volume"]
        raw_rows = await self._scan(columns, limit=limit, sort_by="volume")
        out: list[TradingViewRow] = []
        seen: set[str] = set()
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            d = row.get("d")
            if not isinstance(d, list) or len(d) < 4:
                continue
            canonical = _canonical(str(row.get("s") or (d[0] if d else "")))
            if canonical is None or canonical in seen:
                continue
            seen.add(canonical)
            out.append(
                TradingViewRow(
                    symbol=canonical,
                    raw_symbol=str(d[0]),
                    close=_fnum(d[1]),
                    change_pct=_fnum(d[2]),
                    rating=_fnum(d[3]),
                )
            )
        return out

    async def top_symbols_by_volume(self, limit: int = 50) -> list[str]:
        """Canonical symbols by volume — same shape as the sanctioned adapters."""
        return [r.symbol for r in await self.top_rows(limit=limit)]


def _fnum(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def rating_label(rating: float | None) -> str:
    """TV-convention label for a Recommend.All value."""
    if rating is None:
        return "unknown"
    if rating >= RATING_STRONG_BUY:
        return "strong_buy"
    if rating >= RATING_BUY:
        return "buy"
    if rating <= -RATING_STRONG_BUY:
        return "strong_sell"
    if rating <= -RATING_BUY:
        return "sell"
    return "neutral"
