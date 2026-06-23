"""Historical OHLCV backfill loader — paginates a fetch callable over windows.

Network-agnostic by design: the actual klines fetch is INJECTED as ``fetch`` so
that pagination, de-duplication, chronological ordering, and gap accounting are
unit-testable without any I/O. Production wiring passes a callable backed by
``BinanceAdapter.get_ohlcv(..., start_time_ms=...)``.

De-dup / ordering rely on the UTC ISO-8601 ``timestamp_utc`` produced by the
market-data adapters (fixed ``+00:00`` offset → lexical order == chronological).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.market_data.kline_windows import interval_to_ms, plan_kline_windows
from app.market_data.models import OHLCV

# fetch(symbol, timeframe, start_time_ms, limit) -> candles starting at start_time_ms
FetchKlines = Callable[[str, str, int, int], Awaitable[list[OHLCV]]]


@dataclass(frozen=True)
class OHLCVHistory:
    """Result of a historical backfill over [start_ms, end_ms]."""

    symbol: str
    timeframe: str
    candles: list[OHLCV]  # chronological, de-duplicated
    expected_bars: int  # candles the range should contain
    received_bars: int  # distinct candles actually returned
    gap_bars: int  # max(0, expected - received)

    @property
    def is_complete(self) -> bool:
        """True when every expected candle was received (no gaps)."""
        return self.gap_bars == 0


async def load_ohlcv_history(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    fetch: FetchKlines,
    max_limit: int = 1000,
) -> OHLCVHistory:
    """Backfill OHLCV over a historical range by paginating ``fetch``.

    Args:
        symbol: KAI canonical symbol (e.g. "BTC/USDT").
        timeframe: candle timeframe (e.g. "1h").
        start_ms / end_ms: inclusive range bounds (ms since epoch).
        fetch: injected async klines fetcher (symbol, timeframe, start_ms, limit).
        max_limit: per-request candle cap (Binance hard cap 1000).

    Returns:
        OHLCVHistory with chronologically ordered, de-duplicated candles plus
        expected/received/gap accounting so callers can detect missing data.

    Raises:
        ValueError: unsupported timeframe, or start_ms > end_ms.
    """
    interval_ms = interval_to_ms(timeframe)
    windows = plan_kline_windows(start_ms, end_ms, interval_ms, max_limit=max_limit)
    expected = (end_ms - start_ms) // interval_ms + 1

    by_timestamp: dict[str, OHLCV] = {}
    for window_start, limit in windows:
        rows = await fetch(symbol, timeframe, window_start, limit)
        for candle in rows:
            by_timestamp[candle.timestamp_utc] = candle

    ordered = [by_timestamp[ts] for ts in sorted(by_timestamp)]
    received = len(ordered)
    return OHLCVHistory(
        symbol=symbol,
        timeframe=timeframe,
        candles=ordered,
        expected_bars=expected,
        received_bars=received,
        gap_bars=max(0, expected - received),
    )
