"""I/O layer: fetch canonical-venue (Binance) metrics + orchestrate verdicts.

Fail-soft by contract: any per-symbol fetch error yields ``None`` metrics (→
ineligible via the Honesty-Contract), never an exception — so a venue outage
degrades to "nothing eligible" rather than crashing the caller.
"""

from __future__ import annotations

from typing import Protocol

from app.market_data.models import OHLCV, Ticker
from app.trading.symbol_eligibility import (
    DEFAULT_MIN_HISTORY_DAYS,
    DEFAULT_MIN_TURNOVER_USD,
    EligibilityVerdict,
    SymbolMetrics,
    evaluate_eligibility,
    resolve_duplicates,
)

# Fetch a little more history than the floor so the count is unambiguous.
_HISTORY_BUFFER = 5


class EligibilitySource(Protocol):
    """Structural type satisfied by ``BinanceAdapter``."""

    async def get_ticker(self, symbol: str) -> Ticker | None: ...

    async def get_ohlcv(
        self, symbol: str, timeframe: str = ..., limit: int = ...
    ) -> list[OHLCV]: ...


async def fetch_metrics(
    source: EligibilitySource, symbol: str, *, min_history_days: int
) -> SymbolMetrics:
    """Fetch turnover (volume_24h × last) + history-day count. Fail-soft → None."""
    base, _, quote = symbol.partition("/")
    quote = quote.split(":", 1)[0]

    turnover: float | None = None
    try:
        ticker = await source.get_ticker(symbol)
    except Exception:  # noqa: BLE001 — fail-soft: not measurable
        ticker = None
    if ticker is not None and ticker.last > 0 and ticker.volume_24h >= 0:
        turnover = ticker.volume_24h * ticker.last

    history: int | None = None
    try:
        candles = await source.get_ohlcv(symbol, "1d", min_history_days + _HISTORY_BUFFER)
    except Exception:  # noqa: BLE001 — fail-soft: not measurable
        candles = []
    if candles:
        history = len(candles)

    return SymbolMetrics(
        symbol=symbol,
        base=base,
        quote=quote,
        turnover_24h_usd=turnover,
        history_days=history,
    )


async def build_eligibility(
    source: EligibilitySource,
    symbols: list[str],
    *,
    min_turnover_usd: float = DEFAULT_MIN_TURNOVER_USD,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
) -> list[EligibilityVerdict]:
    """Fetch metrics for every symbol, resolve duplicates, decide eligibility."""
    dup_map = resolve_duplicates(symbols)
    verdicts: list[EligibilityVerdict] = []
    for symbol in symbols:
        metrics = await fetch_metrics(source, symbol, min_history_days=min_history_days)
        verdicts.append(
            evaluate_eligibility(
                metrics,
                min_turnover_usd=min_turnover_usd,
                min_history_days=min_history_days,
                duplicate_of=dup_map.get(symbol),
            )
        )
    return verdicts
