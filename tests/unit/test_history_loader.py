"""Historical OHLCV backfill loader tests.

The fetch is injected, so these tests exercise pagination, de-duplication,
ordering, and gap accounting deterministically with NO network.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.market_data.history_loader import load_ohlcv_history
from app.market_data.models import OHLCV

_H = 3_600_000  # 1h in ms


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _candle(ms: int) -> OHLCV:
    price = 100.0 + ms / _H
    return OHLCV(
        symbol="BTC/USDT",
        timestamp_utc=_iso(ms),
        timeframe="1h",
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1.0,
    )


def _complete_fetcher(start_ms: int, interval_ms: int, available_bars: int):
    """Fake that serves contiguous candles from a finite backing series."""
    last_ms = start_ms + (available_bars - 1) * interval_ms

    async def fetch(symbol: str, timeframe: str, window_start: int, limit: int):
        out: list[OHLCV] = []
        for k in range(limit):
            t = window_start + k * interval_ms
            if t > last_ms:
                break
            out.append(_candle(t))
        return out

    return fetch


async def test_full_backfill_across_windows_no_gaps() -> None:
    fetch = _complete_fetcher(0, _H, available_bars=2500)
    hist = await load_ohlcv_history("BTC/USDT", "1h", 0, 2499 * _H, fetch, max_limit=1000)
    assert hist.expected_bars == 2500
    assert hist.received_bars == 2500
    assert hist.gap_bars == 0
    assert hist.is_complete
    # Chronological and unique.
    ts = [c.timestamp_utc for c in hist.candles]
    assert ts == sorted(ts)
    assert len(set(ts)) == 2500


async def test_dedup_keeps_each_timestamp_once() -> None:
    # Fake ignores the window and always returns the same two candles; forcing
    # two windows (max_limit=1) means they are returned twice → must de-dup.
    async def fetch(symbol: str, timeframe: str, window_start: int, limit: int):
        return [_candle(0), _candle(_H)]

    hist = await load_ohlcv_history("BTC/USDT", "1h", 0, _H, fetch, max_limit=1)
    assert hist.received_bars == 2
    assert len(hist.candles) == 2
    assert hist.is_complete


async def test_orders_unsorted_fetch_output() -> None:
    async def fetch(symbol: str, timeframe: str, window_start: int, limit: int):
        return [_candle(2 * _H), _candle(0), _candle(_H)]

    hist = await load_ohlcv_history("BTC/USDT", "1h", 0, 2 * _H, fetch, max_limit=1000)
    closes_ts = [c.timestamp_utc for c in hist.candles]
    assert closes_ts == [_iso(0), _iso(_H), _iso(2 * _H)]


async def test_gap_accounting_when_data_missing() -> None:
    # Range expects 5 bars; backing series only has 3 → gap of 2.
    fetch = _complete_fetcher(0, _H, available_bars=3)
    hist = await load_ohlcv_history("BTC/USDT", "1h", 0, 4 * _H, fetch, max_limit=1000)
    assert hist.expected_bars == 5
    assert hist.received_bars == 3
    assert hist.gap_bars == 2
    assert not hist.is_complete


async def test_empty_fetch_reports_full_gap() -> None:
    async def fetch(symbol: str, timeframe: str, window_start: int, limit: int):
        return []

    hist = await load_ohlcv_history("BTC/USDT", "1h", 0, 9 * _H, fetch, max_limit=1000)
    assert hist.expected_bars == 10
    assert hist.received_bars == 0
    assert hist.gap_bars == 10
    assert hist.candles == []
