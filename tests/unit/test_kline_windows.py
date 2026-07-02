"""Kline backfill window-planner tests.

The planner must cover every candle in a range exactly once, respecting the
per-request max_limit, with no I/O.
"""

from __future__ import annotations

import pytest

from app.market_data.kline_windows import interval_to_ms, plan_kline_windows

_H = 3_600_000  # 1h in ms


def test_interval_to_ms_known_values() -> None:
    assert interval_to_ms("1m") == 60_000
    assert interval_to_ms("1h") == 3_600_000
    assert interval_to_ms("1d") == 86_400_000


def test_interval_to_ms_unsupported_raises() -> None:
    with pytest.raises(ValueError):
        interval_to_ms("3w")


def test_single_bar_range() -> None:
    assert plan_kline_windows(0, 0, _H) == [(0, 1)]


def test_range_within_one_window() -> None:
    # 5 hourly bars (indices 0..4) → one window of 5.
    assert plan_kline_windows(0, 4 * _H, _H, max_limit=1000) == [(0, 5)]


def test_range_exactly_max_limit() -> None:
    # 1000 bars fit one window.
    windows = plan_kline_windows(0, 999 * _H, _H, max_limit=1000)
    assert windows == [(0, 1000)]


def test_range_one_over_max_limit_splits() -> None:
    # 1001 bars → 1000 + 1, second window starts after 1000 intervals.
    windows = plan_kline_windows(0, 1000 * _H, _H, max_limit=1000)
    assert windows == [(0, 1000), (1000 * _H, 1)]


def test_multi_window_covers_every_bar_once() -> None:
    # 2500 bars, max 1000 → 1000 + 1000 + 500, contiguous, no overlap/gap.
    windows = plan_kline_windows(0, 2499 * _H, _H, max_limit=1000)
    assert windows == [(0, 1000), (1000 * _H, 1000), (2000 * _H, 500)]
    # The covered bar count equals the total bars in the range.
    assert sum(limit for _, limit in windows) == 2500
    # Each window starts exactly where the previous one ended (contiguous).
    for (s0, lim0), (s1, _lim1) in zip(windows, windows[1:], strict=False):
        assert s1 == s0 + lim0 * _H


def test_start_after_end_raises() -> None:
    with pytest.raises(ValueError):
        plan_kline_windows(10, 5, _H)


def test_invalid_interval_raises() -> None:
    with pytest.raises(ValueError):
        plan_kline_windows(0, 10, 0)


def test_invalid_max_limit_raises() -> None:
    with pytest.raises(ValueError):
        plan_kline_windows(0, 10, _H, max_limit=0)
