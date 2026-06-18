"""TradingView market-data adapter — price source via the TV scanner datafeed.

Reduces sole-CoinGecko dependence and lets KAI resolve symbols the crypto venues
+ CoinGecko don't list (the operator's TV Pro covers far more pairs). Wraps the
existing :class:`TradingViewDatafeed` (scanner). Read-only and **fail-soft**
(``None``/``[]`` on any miss or error) so it can never raise into the price
cascade.

Placement: inserted ADDITIVELY at the END of the fallback chain (before the
synthetic Mock), behind a default-off flag. Reason: the TV scanner is an
UNOFFICIAL endpoint (ToS gray-area, may break / rate-limit) — it must never be
the sole/primary live-price path, only the resolver for what the robust venues
cannot quote.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.integrations.tradingview.datafeed import TradingViewDatafeed
from app.market_data.base import BaseMarketDataAdapter
from app.market_data.models import OHLCV, Ticker

logger = logging.getLogger(__name__)

_DEFAULT_EXCHANGE = "BYBIT"


class TradingViewMarketDataAdapter(BaseMarketDataAdapter):
    """Resolve a symbol's last price from the TradingView scanner (``close``)."""

    def __init__(
        self,
        *,
        exchange: str = _DEFAULT_EXCHANGE,
        timeout_seconds: int = 10,
        datafeed: TradingViewDatafeed | None = None,
    ) -> None:
        self._feed = datafeed or TradingViewDatafeed(
            exchange=exchange, timeout_seconds=timeout_seconds
        )

    @property
    def adapter_name(self) -> str:
        return "tradingview"

    async def get_ticker(self, symbol: str) -> Ticker | None:
        canonical = symbol.strip().upper()
        # The scanner ticker is built from BASE/QUOTE; without the slash we cannot
        # form it → let the rest of the cascade handle it (fail-soft).
        if "/" not in canonical:
            return None
        try:
            snap = await self._feed.technicals([canonical], columns=("close",))
        except Exception as exc:  # noqa: BLE001 — a price source must never raise into the chain
            logger.warning("tradingview_adapter.technicals_failed: %s", exc)
            return None
        close = (snap.get(canonical) or {}).get("close")
        if not isinstance(close, (int, float)) or close <= 0:
            return None
        return Ticker(
            symbol=canonical,
            timestamp_utc=datetime.now(UTC).isoformat(),
            bid=float(close),
            ask=float(close),
            last=float(close),
            volume_24h=0.0,
        )

    async def get_price(self, symbol: str) -> float | None:
        ticker = await self.get_ticker(symbol)
        return ticker.last if ticker is not None else None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[OHLCV]:
        # The scanner returns point-in-time values, not OHLCV history → fail-soft;
        # other adapters in the cascade supply candles where needed.
        return []


__all__ = ["TradingViewMarketDataAdapter"]
