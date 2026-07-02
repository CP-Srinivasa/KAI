"""Causal as-of alignment of scheduled token-unlock pressure onto OHLCV bars.

Token vesting/unlock schedules are PUBLIC IN ADVANCE. Conditioning a bar at time
``t`` on "how many tokens are scheduled to unlock in the next ``horizon_days``" is
therefore causal w.r.t. INFORMATION — the schedule was known at ``t``; only the
price outcome is unknown. This is the standard "known future event" setup (like an
announced earnings date), NOT price look-ahead: the feature reads scheduled unlock
amounts, never any future price.

``OHLCV.timestamp_utc`` is the bar OPEN time. For each bar we sum the token amount
of unlock events scheduled in the FORWARD window ``(bar_open, bar_open + horizon]``
— the "imminent unlock pressure" approaching that bar — and z-score it over the
token's recent history (regime-relative, since absolute unlock sizes vary wildly by
token). The series ramps up as a large cliff approaches and falls once it passes, so
it naturally encodes "a big unlock is coming".

Caveat (documented, not ignored): DefiLlama serves the CURRENT schedule; a token
whose plan was later revised carries a mild look-ahead on the revision. The v1
universe uses fixed cliff/linear schedules where this is small.

Pure module: no I/O, no network. Fetching/parsing the schedule lives in
``scripts/build_unlock_events.py``; this only aligns what it is given.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

# Forward window for "imminent" unlock pressure. The documented unlock-short edge
# acts on a ~72h–7d horizon; 7 days captures the approach to a cliff.
UNLOCK_HORIZON_DAYS = 7
# Rolling window (bars) for the unlock-pressure z-score: ~30 days at 1h bars —
# spans the quiet baseline between unlocks and the ramp into one.
UNLOCK_Z_WINDOW = 720
# Minimum defined points before a z is emitted; below this the z is None (decider
# treats None as "no trade"), never a fabricated 0.
UNLOCK_Z_MIN_POINTS = 48


@dataclass(frozen=True)
class UnlockEvent:
    """One scheduled unlock: ``amount_tokens`` entering supply at ``event_ms`` (UTC)."""

    event_ms: int
    amount_tokens: float


def _iso_to_ms(ts: str) -> int | None:
    """Parse an ISO-8601 bar timestamp to epoch ms (UTC for naive); None if unparseable."""
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _rolling_z(window: list[float], value: float) -> float | None:
    """Population z-score of ``value`` within ``window``; None if undefined."""
    if len(window) < UNLOCK_Z_MIN_POINTS:
        return None
    mean = statistics.fmean(window)
    std = statistics.pstdev(window)
    if std <= 0.0:
        return None
    return (value - mean) / std


def align_unlock_to_bars(
    bar_timestamps_utc: Sequence[str],
    events: Sequence[UnlockEvent],
    max_supply: float | None,
    *,
    horizon_days: int = UNLOCK_HORIZON_DAYS,
    z_window: int = UNLOCK_Z_WINDOW,
) -> tuple[list[float | None], list[float | None]]:
    """Map scheduled unlocks to a per-bar forward unlock-pressure fraction + z.

    Args:
        bar_timestamps_utc: oldest-first bar OPEN timestamps (ISO-8601), assumed
            monotonically non-decreasing (as Binance klines are).
        events: scheduled unlock events (any order; sorted internally).
        max_supply: token max supply, the fraction denominator. When None or
            non-positive, both outputs are all None (cannot normalise).
        horizon_days: forward window (days) over which to sum imminent unlocks.
        z_window: number of recent bars for the pressure z-score.

    Returns:
        ``(unlock_frac_fwd, unlock_frac_fwd_z)`` aligned 1:1. ``unlock_frac_fwd`` is
        the fraction of max supply scheduled to unlock in ``(bar_open, bar_open +
        horizon]`` — 0.0 is a real value ("nothing imminent"), never None once
        ``max_supply`` is known. The z is None until ``UNLOCK_Z_MIN_POINTS`` points.
    """
    n = len(bar_timestamps_utc)
    frac: list[float | None] = [None] * n
    frac_z: list[float | None] = [None] * n
    if not events or n == 0 or max_supply is None or max_supply <= 0.0:
        return frac, frac_z

    pts = sorted(events, key=lambda e: e.event_ms)
    ev_ms = [e.event_ms for e in pts]
    ev_amt = [e.amount_tokens for e in pts]
    m = len(pts)
    window_ms = horizon_days * 86_400_000

    lo = 0  # first event still strictly after the current bar (upcoming)
    hi = 0  # first event beyond the forward window
    running = 0.0
    z_hist: list[float] = []
    for i, ts in enumerate(bar_timestamps_utc):
        bar_ms = _iso_to_ms(ts)
        if bar_ms is None:
            continue
        upper = bar_ms + window_ms
        # Admit events scheduled within the forward window.
        while hi < m and ev_ms[hi] <= upper:
            running += ev_amt[hi]
            hi += 1
        # Evict events that are now at/in the past (no longer "upcoming").
        while lo < hi and ev_ms[lo] <= bar_ms:
            running -= ev_amt[lo]
            lo += 1
        value = running / max_supply
        frac[i] = value
        z_hist.append(value)
        frac_z[i] = _rolling_z(z_hist[-z_window:], value)
    return frac, frac_z
