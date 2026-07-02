"""Causal as-of alignment of sparse perpetual funding onto dense OHLCV bars.

Perpetual funding settles on its own cadence (Binance: every 8h at 00/08/16 UTC),
far sparser than the 1h/1d research bars. To condition a backtest on funding we
must map each settled funding event onto the bar timeline WITHOUT look-ahead:

    a bar opening at time ``t`` may only see funding SETTLED AT OR BEFORE ``t``.

``OHLCV.timestamp_utc`` is the bar's OPEN time (see ``binance_adapter``), so a
funding event settled before the bar opens is certainly known by the time we
would act at that bar's close — the strict ``settlement_ms <= bar_open_ms`` rule
is the conservative causal choice and is asserted in
``tests/unit/test_funding_align.py``.

Two causal features come out per bar:
  * ``rate``  — the most recent settled funding rate as of the bar (forward-fill);
  * ``rate_z`` — a rolling z-score of that rate over the last ``z_window`` settled
    events known as of the bar (funding regimes drift, so a self-relative z is far
    more robust than an absolute bp threshold for conditioning).

Pure module: no I/O, no network. The historical funding fetch lives in the
market-data adapter; this only aligns what it returns.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

# Rolling window (number of settled funding events) for the funding z-score.
# 24 eight-hour settlements ≈ 8 days of funding regime — enough to characterise
# the current regime, short enough to react to a shift.
FUNDING_Z_WINDOW = 24
# Minimum settlements in the window before a z-score is defined; below this the
# z is None (decider treats None as "no trade"), never a fabricated 0.
FUNDING_Z_MIN_POINTS = 3


@dataclass(frozen=True)
class FundingPoint:
    """One settled perpetual funding event.

    ``settlement_ms`` is the funding settlement time (epoch ms, UTC); ``rate`` is
    the funding rate as a fraction per interval (Binance native; 0.0001 = 1 bp/8h).
    """

    settlement_ms: int
    rate: float


def _iso_to_ms(ts: str) -> int | None:
    """Parse an ISO-8601 timestamp to epoch ms, forcing UTC for naive inputs.

    Returns None for anything that does not parse (e.g. synthetic ``"bar-3"``
    labels) so alignment degrades to "no funding" rather than raising.
    """
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _rolling_z(window: list[float], value: float) -> float | None:
    """Population z-score of ``value`` within ``window``; None if undefined.

    None when the window has fewer than ``FUNDING_Z_MIN_POINTS`` points or zero
    dispersion (a constant funding regime has no meaningful extreme).
    """
    if len(window) < FUNDING_Z_MIN_POINTS:
        return None
    mean = statistics.fmean(window)
    std = statistics.pstdev(window)
    if std <= 0.0:
        return None
    return (value - mean) / std


def align_funding_to_bars(
    bar_timestamps_utc: Sequence[str],
    funding: Sequence[FundingPoint],
    *,
    z_window: int = FUNDING_Z_WINDOW,
) -> tuple[list[float | None], list[float | None]]:
    """Forward-fill funding onto bars causally and derive a rolling z-score.

    Args:
        bar_timestamps_utc: oldest-first bar OPEN timestamps (ISO-8601). Assumed
            monotonically non-decreasing (as Binance klines are).
        funding: settled funding events (any order; sorted internally).
        z_window: number of recent settlements used for the funding z-score.

    Returns:
        ``(rate, rate_z)`` each aligned 1:1 to ``bar_timestamps_utc``. For a bar
        that precedes the first settlement (or whose timestamp does not parse)
        both are None. ``rate`` is the as-of settled rate; ``rate_z`` is None until
        ``FUNDING_Z_MIN_POINTS`` settlements are known.
    """
    n = len(bar_timestamps_utc)
    rate: list[float | None] = [None] * n
    rate_z: list[float | None] = [None] * n
    if not funding or n == 0:
        return rate, rate_z

    pts = sorted(funding, key=lambda p: p.settlement_ms)
    settle_ms = [p.settlement_ms for p in pts]
    rates = [p.rate for p in pts]
    m = len(pts)

    j = -1  # index of the latest settlement at or before the current bar
    for i, ts in enumerate(bar_timestamps_utc):
        bar_ms = _iso_to_ms(ts)
        if bar_ms is None:
            continue
        # Two-pointer advance: bars are non-decreasing so j only moves forward.
        while j + 1 < m and settle_ms[j + 1] <= bar_ms:
            j += 1
        if j < 0:
            continue  # bar precedes the first known funding settlement
        rate[i] = rates[j]
        lo = max(0, j - z_window + 1)
        rate_z[i] = _rolling_z(rates[lo : j + 1], rates[j])
    return rate, rate_z
