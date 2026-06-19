"""Read-only market data service helpers for CLI/MCP surfaces."""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from datetime import UTC, datetime

from app.market_data.base import BaseMarketDataAdapter
from app.market_data.binance_futures_adapter import BinanceFuturesAdapter
from app.market_data.bitmex_adapter import BitMEXAdapter
from app.market_data.bybit_adapter import BybitAdapter
from app.market_data.coingecko_adapter import CoinGeckoAdapter
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.market_data.models import OHLCV, MarketDataPoint, MarketDataSnapshot, Ticker
from app.market_data.okx_adapter import OKXAdapter

logger = logging.getLogger(__name__)

# DS-20260529-V1: default cross-provider disagreement tolerance for the
# fallback chain. Spot/futures venues agree on a liquid pair to well within
# 1%; a divergence beyond this floor means at least one provider is pricing a
# stale or wrong instrument (e.g. BitMEX kept a delisted "MATIC" ticker at
# 0.40875 after the POL rebrand while every other venue priced ~0.088). The
# 2026-05-28 paper book booked +73,548 USD of phantom PnL because entry and
# monitor ticks were priced by *different* providers on this disagreement.
# Env-tunable via MARKET_DATA_PROVIDER_DISAGREEMENT_PCT (fraction, e.g. 0.10).
_DEFAULT_PROVIDER_DISAGREEMENT_PCT = 0.10

# MockMarketDataAdapter.adapter_name. Synthetic last-resort data: it must never
# corroborate or veto a real venue in the disagreement cross-check (see
# get_market_data_point). Pinned against drift by a unit test.
_MOCK_SOURCE = "mock"


def _provider_disagreement_pct() -> float:
    raw = os.environ.get("MARKET_DATA_PROVIDER_DISAGREEMENT_PCT")
    if raw is None:
        return _DEFAULT_PROVIDER_DISAGREEMENT_PCT
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_PROVIDER_DISAGREEMENT_PCT
    return value if value > 0 else _DEFAULT_PROVIDER_DISAGREEMENT_PCT


class FallbackMarketDataAdapter(BaseMarketDataAdapter):
    """Tries each underlying adapter in order, returns the first available.

    Built for the operator-signal bridge (V25-D, 2026-05-05): the premium
    Telegram channel posts Bybit-Futures pairs that include exotic tokens
    CoinGecko does not list. We therefore query Bybit first; if Bybit
    returns no data (symbol not found, transport error, rate limit), we
    fall back to CoinGecko for the well-known majors. Mock is the last
    resort so a smoke-test never crashes for missing market data.

    The chain order is intentional: Bybit is authoritative for the symbols
    the bridge actually sees in production. CoinGecko only covers a subset
    but uses different rate-limit pools, so it adds true redundancy.
    """

    def __init__(
        self,
        adapters: list[BaseMarketDataAdapter],
        *,
        disagreement_pct: float | None = None,
    ) -> None:
        if not adapters:
            raise ValueError("FallbackMarketDataAdapter requires >=1 adapter")
        self._adapters = adapters
        self._disagreement_pct = (
            disagreement_pct if disagreement_pct is not None else _provider_disagreement_pct()
        )

    @property
    def adapter_name(self) -> str:
        return "fallback:" + ",".join(a.adapter_name for a in self._adapters)

    async def get_ticker(self, symbol: str) -> Ticker | None:
        for adapter in self._adapters:
            ticker = await adapter.get_ticker(symbol)
            if ticker is not None and ticker.last > 0:
                return ticker
        return None

    async def get_price(self, symbol: str) -> float | None:
        ticker = await self.get_ticker(symbol)
        return ticker.last if ticker is not None else None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[OHLCV]:
        for adapter in self._adapters:
            data = await adapter.get_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if data:
                return data
        return []

    async def top_symbols_by_volume(self, limit: int = 50) -> list[str]:
        for adapter in self._adapters:
            symbols = await adapter.top_symbols_by_volume(limit)
            if symbols:
                return symbols
        return []

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
        """Return the first usable provider point, cross-checked for sanity.

        DS-20260529-V1: the chain still resolves in priority order, but before
        a point is trusted we collect at least one corroborating provider. If
        two fresh providers disagree on price beyond ``self._disagreement_pct``
        the symbol is being priced off a stale/wrong instrument on one venue —
        we return the candidate tagged ``is_stale=True`` so the trading loop
        (entry) and the position monitor (exit) both SKIP it instead of opening
        or closing a position at a phantom price. Single-provider symbols can't
        be cross-checked and are returned best-effort, unchanged.
        """
        resolved: list[MarketDataPoint] = []
        for adapter in self._adapters:
            point = await adapter.get_market_data_point(symbol)
            if point is not None and point.price > 0:
                resolved.append(point)
                # Early-stop quorum counts only REAL fresh quotes. Synthetic
                # mock must not satisfy the 2-provider cross-check, else we stop
                # querying real venues the moment mock chimes in and let a
                # phantom quote veto a real one.
                fresh_real = [p for p in resolved if not p.is_stale and p.source != _MOCK_SOURCE]
                if len(fresh_real) >= 2:
                    break

        if not resolved:
            return None

        # Synthetic mock data is a last-resort price source ONLY: it must never
        # corroborate or veto a real venue. Otherwise a single-real-venue micro-
        # cap (SKYAI, COAI, …) is permanently disagreement-stale-tagged by the
        # mock's phantom second opinion and can be neither entered NOR exited.
        # (2026-06-14: a SKYAI paper position was frozen at ~40% of the book this
        # way — binance_futures 0.355 vs mock 101.3 → is_stale forever.)
        real = [p for p in resolved if p.source != _MOCK_SOURCE]
        fresh = [p for p in resolved if not p.is_stale]
        fresh_real = [p for p in real if not p.is_stale]

        # Prefer a fresh real quote; then any real quote; only fall through to a
        # mock point when no real provider produced one at all.
        chosen = (fresh_real or real or fresh or resolved)[0]

        # Cross-check disagreement among REAL fresh providers only — the
        # MATIC/POL delisting protection (DS-20260529-V1) still fires when two
        # genuine venues disagree, but mock can no longer trigger it.
        if len(fresh_real) >= 2:
            prices = [p.price for p in fresh_real]
            lo, hi = min(prices), max(prices)
            if lo > 0 and (hi / lo - 1.0) > self._disagreement_pct:
                logger.warning(
                    "[MARKET_DATA] provider disagreement for %s: %.6g..%.6g "
                    "(>%.0f%%) — tagging stale so entry/monitor skip. providers=%s",
                    symbol,
                    lo,
                    hi,
                    self._disagreement_pct * 100,
                    [p.source for p in fresh_real],
                )
                return replace(
                    chosen,
                    is_stale=True,
                    source=f"{chosen.source}|provider_disagreement:{lo:.6g}vs{hi:.6g}",
                )
        return chosen


def create_market_data_adapter(
    *,
    provider: str,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    api_key: str | None = None,
) -> BaseMarketDataAdapter:
    """Create a market data adapter by provider name.

    Provider values:
    - 'bybit'           : Bybit V5 linear (futures) — broadest premium-channel
                          symbol coverage; primary source.
    - 'binance_futures' : Binance USD-M futures (fapi.binance.com) — full
                          coverage backup with same symbol convention.
    - 'okx'             : OKX V5 perpetual swap (BTC-USDT-SWAP convention) —
                          mainstream-token redundancy.
    - 'bitmex'          : BitMEX instrument ticker (XBT prefix for BTC) —
                          BTC + major-coin redundancy.
    - 'coingecko'       : CoinGecko spot aggregation — broad token list,
                          slower, misses many Bybit-exclusive pairs.
    - 'fallback'        : Try Bybit → Binance Futures → OKX → BitMEX →
                          CoinGecko → Mock. RECOMMENDED for the operator
                          bridge — matches the channel name "Bitmex/Bybit/
                          Futures/OKX Premium Signals" exactly so any signal
                          for any of those venues resolves on the first
                          adapter that knows the symbol.
    - 'mock'            : Synthetic test data only.
    """
    normalized = provider.strip().lower()
    if normalized == "bybit":
        return BybitAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    if normalized in ("binance_futures", "binance-futures", "binancefutures"):
        return BinanceFuturesAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    if normalized == "okx":
        return OKXAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    if normalized == "bitmex":
        return BitMEXAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    if normalized == "coingecko":
        if api_key is None:
            from app.core.settings import get_settings

            api_key = get_settings().coingecko_api_key
        return CoinGeckoAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
            api_key=api_key or None,
        )
    if normalized == "fallback":
        if api_key is None:
            from app.core.settings import get_settings

            api_key = get_settings().coingecko_api_key
        chain: list[BaseMarketDataAdapter] = [
            BybitAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            ),
            BinanceFuturesAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            ),
            OKXAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            ),
            BitMEXAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            ),
            CoinGeckoAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
                api_key=api_key or None,
            ),
        ]
        # TradingView price fallback (default-OFF): resolves symbols the crypto
        # venues + CoinGecko don't quote (the operator's TV Pro covers far more
        # pairs / RWA). Inserted ADDITIVELY before Mock so synthetic data stays
        # the true last resort. The TV scanner is an UNOFFICIAL endpoint
        # (ToS gray-area, may break) → never primary; opt-in via
        # APP_TRADINGVIEW_PRICE_FALLBACK_ENABLED.
        from app.core.settings import get_settings as _get_settings

        _settings = _get_settings()
        if _settings.tradingview_price_fallback_enabled:
            from app.market_data.tradingview_adapter import TradingViewMarketDataAdapter

            chain.append(
                TradingViewMarketDataAdapter(
                    exchange=_settings.tradingview.datafeed_exchange,
                    timeout_seconds=timeout_seconds,
                )
            )
        chain.append(
            MockMarketDataAdapter(
                freshness_threshold_seconds=freshness_threshold_seconds,
            )
        )
        return FallbackMarketDataAdapter(chain)
    if normalized == "mock":
        logger.warning(
            "market_data_provider=mock: using synthetic mock data. "
            "Set APP_MARKET_DATA_PROVIDER=fallback for real market data."
        )
        return MockMarketDataAdapter(
            freshness_threshold_seconds=freshness_threshold_seconds,
        )
    raise ValueError(f"unsupported_provider:{provider}")


async def get_market_data_snapshot(
    *,
    symbol: str,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> MarketDataSnapshot:
    retrieved_at = datetime.now(UTC).isoformat()
    try:
        adapter = create_market_data_adapter(
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        return MarketDataSnapshot(
            symbol=symbol,
            provider=provider,
            retrieved_at_utc=retrieved_at,
            source_timestamp_utc=None,
            price=None,
            is_stale=True,
            freshness_seconds=None,
            available=False,
            error=str(exc),
        )

    return await adapter.get_market_data_snapshot(symbol)
