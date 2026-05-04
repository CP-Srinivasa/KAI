"""Tests for the rolling-window rpm counter in the CoinGecko adapter.

The counter is process-wide (module-level deque) and purely observational
— it never throttles.  We verify: counting, pruning, threshold-warning,
and warning-throttle.
"""

from __future__ import annotations

import logging
import time

import pytest

from app.market_data import coingecko_adapter as cg


@pytest.fixture(autouse=True)
def _reset_counter_state() -> None:
    """Reset module-level state so each test starts from zero."""
    cg._REQUEST_TIMESTAMPS.clear()
    cg._last_rate_warn_ts = 0.0
    yield
    cg._REQUEST_TIMESTAMPS.clear()
    cg._last_rate_warn_ts = 0.0


def test_counter_counts_requests_in_window() -> None:
    for _ in range(10):
        rpm = cg._record_request_and_maybe_warn()
    assert rpm == 10
    assert len(cg._REQUEST_TIMESTAMPS) == 10


def test_counter_prunes_entries_older_than_window() -> None:
    now = time.monotonic()
    # Inject 5 fake-old timestamps (2x the window in the past)
    for _ in range(5):
        cg._REQUEST_TIMESTAMPS.append(now - 2 * cg._REQUEST_WINDOW_SECONDS)
    # Now one real call — pruning should drop all 5 stale entries.
    rpm = cg._record_request_and_maybe_warn()
    assert rpm == 1


def test_warning_fires_when_rpm_crosses_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=cg.logger.name)
    # One shy of threshold — no warn.
    for _ in range(cg._RATE_WARN_THRESHOLD_RPM - 1):
        cg._record_request_and_maybe_warn()
    assert not any("crossed warn threshold" in r.message for r in caplog.records)

    # The threshold-th call triggers the warn.
    cg._record_request_and_maybe_warn()
    matching = [r for r in caplog.records if "crossed warn threshold" in r.message]
    assert len(matching) == 1
    assert f"rpm={cg._RATE_WARN_THRESHOLD_RPM}" in matching[0].message


def test_warning_is_throttled_within_min_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Once the warn fires, further crossings in the same minute must not
    re-emit — otherwise a sustained overload would spam logs every call."""
    caplog.set_level(logging.WARNING, logger=cg.logger.name)
    # Drive the counter past the threshold.
    for _ in range(cg._RATE_WARN_THRESHOLD_RPM + 50):
        cg._record_request_and_maybe_warn()
    count_first = sum(1 for r in caplog.records if "crossed warn threshold" in r.message)
    assert count_first == 1, f"expected single warn, got {count_first}"
