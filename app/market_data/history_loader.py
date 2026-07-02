"""Historical OHLCV backfill loader — paginates a fetch callable over windows.

Network-agnostic by design: the actual klines fetch is INJECTED as ``fetch`` so
that pagination, de-duplication, chronological ordering, range-filtering, and
gap accounting are unit-testable without any I/O. Production wiring passes a
callable backed by ``BinanceAdapter.get_ohlcv(..., start_time_ms=...)``.

Hardening (from the security + correctness audits):
- Bounds are SNAPPED to the candle grid before expected/gap accounting, so an
  off-grid start/end cannot manufacture a phantom gap (NEO-P2).
- A hard ``max_total_bars`` cap bounds memory/time for huge ranges (SAT-P1).
- A failing window is isolated (logged, counted as a gap) instead of aborting
  the whole backfill (SAT-P2).
- Candles outside the requested grid range are dropped, so an exchange overrun
  cannot contaminate the backtest or hide behind the gap count (SAT-P2).

De-dup / ordering rely on the UTC ISO-8601 ``timestamp_utc`` produced by the
market-data adapters (fixed ``+00:00`` offset → lexical order == chronological).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from app.market_data.kline_windows import interval_to_ms, plan_kline_windows
from app.market_data.models import OHLCV

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOTAL_BARS = 200_000

# fetch(symbol, timeframe, start_time_ms, limit) -> candles starting at start_time_ms
FetchKlines = Callable[[str, str, int, int], Awaitable[list[OHLCV]]]


@dataclass(frozen=True)
class OHLCVHistory:
    """Result of a historical backfill over [start_ms, end_ms]."""

    symbol: str
    timeframe: str
    candles: list[OHLCV]  # chronological, de-duplicated, in-range
    expected_bars: int  # candles the grid-snapped range should contain
    received_bars: int  # distinct in-range candles actually returned
    gap_bars: int  # max(0, expected - received)

    @property
    def is_complete(self) -> bool:
        """True when every expected candle was received (no gaps)."""
        return self.gap_bars == 0


def _candle_open_ms(timestamp_utc: str) -> int | None:
    """Parse an adapter ISO timestamp back to epoch ms (None if unparseable)."""
    try:
        return int(round(datetime.fromisoformat(timestamp_utc).timestamp() * 1000))
    except (ValueError, TypeError):
        return None


async def load_ohlcv_history(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    fetch: FetchKlines,
    max_limit: int = 1000,
    max_total_bars: int = DEFAULT_MAX_TOTAL_BARS,
) -> OHLCVHistory:
    """Backfill OHLCV over a historical range by paginating ``fetch``.

    Args:
        symbol: KAI canonical symbol (e.g. "BTC/USDT").
        timeframe: candle timeframe (e.g. "1h").
        start_ms / end_ms: inclusive range bounds (ms since epoch); snapped down
            to the candle grid internally.
        fetch: injected async klines fetcher (symbol, timeframe, start_ms, limit).
        max_limit: per-request candle cap (Binance hard cap 1000).
        max_total_bars: hard upper bound on the range size; larger ranges raise.

    Returns:
        OHLCVHistory with chronologically ordered, de-duplicated, in-range
        candles plus expected/received/gap accounting.

    Raises:
        ValueError: unsupported timeframe, start_ms > end_ms, or a range larger
            than ``max_total_bars``.
    """
    if start_ms > end_ms:
        raise ValueError("start_ms must be <= end_ms")
    interval_ms = interval_to_ms(timeframe)

    # Snap to the candle grid: the exchange returns grid-aligned open times, so
    # accounting must be computed on the grid (else off-grid bounds fake a gap).
    grid_start = start_ms - (start_ms % interval_ms)
    grid_end = end_ms - (end_ms % interval_ms)
    expected = (grid_end - grid_start) // interval_ms + 1
    if expected > max_total_bars:
        raise ValueError(f"range too large: {expected} bars > max_total_bars={max_total_bars}")

    windows = plan_kline_windows(grid_start, grid_end, interval_ms, max_limit=max_limit)

    by_timestamp: dict[str, OHLCV] = {}
    for window_start, limit in windows:
        try:
            rows = await fetch(symbol, timeframe, window_start, limit)
        except Exception as exc:  # noqa: BLE001 — one bad window must not abort the backfill
            logger.warning("backfill window %d (%s) failed: %s", window_start, symbol, exc)
            continue
        for candle in rows:
            open_ms = _candle_open_ms(candle.timestamp_utc)
            if open_ms is None or not (grid_start <= open_ms <= grid_end):
                continue  # drop out-of-range / unparseable candles
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
