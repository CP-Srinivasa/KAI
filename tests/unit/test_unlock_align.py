"""Causal unlock-pressure-to-bar alignment tests.

The risks of mapping a scheduled unlock calendar onto bars:
  1. The feature is a FORWARD sum (unlocks in the next horizon) but must use only
     the public SCHEDULE — never a future price. The schedule is known as-of the bar.
  2. An unlock at/in the past is no longer "upcoming" and must drop out.
  3. Fraction normalisation by max supply; None when supply is unknown.
  4. Z-score warm-up (point count) + zero-variance None.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.analysis.features.unlock_align import (
    UNLOCK_Z_MIN_POINTS,
    UnlockEvent,
    align_unlock_to_bars,
)

_DAY = 86_400_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _days(n: int) -> list[str]:
    return [_iso(i * _DAY) for i in range(n)]


def test_empty_events_yield_all_none() -> None:
    frac, frac_z = align_unlock_to_bars(_days(5), [], 1000.0)
    assert frac == [None] * 5
    assert frac_z == [None] * 5


def test_missing_max_supply_yields_all_none() -> None:
    events = [UnlockEvent(5 * _DAY, 100.0)]
    for bad in (None, 0.0, -1.0):
        frac, frac_z = align_unlock_to_bars(_days(5), events, bad)
        assert frac == [None] * 5
        assert frac_z == [None] * 5


def test_forward_window_sums_upcoming_unlocks() -> None:
    # max_supply 1000; unlocks of 100 at day5 and 300 at day10; horizon 7 days.
    events = [UnlockEvent(5 * _DAY, 100.0), UnlockEvent(10 * _DAY, 300.0)]
    frac, _ = align_unlock_to_bars(_days(15), events, 1000.0, horizon_days=7)
    assert frac[0] == 0.1  # (0,7]  -> day5 only          -> 100/1000
    assert frac[3] == 0.4  # (3,10] -> day5 + day10        -> 400/1000
    assert frac[5] == 0.3  # (5,12] -> day5 is now past    -> 300/1000
    assert frac[10] == 0.0  # (10,17] -> day10 is now past -> nothing upcoming


def test_unlock_at_or_before_bar_is_not_upcoming() -> None:
    # An unlock exactly at the bar open is "now/past", not upcoming → excluded.
    events = [UnlockEvent(5 * _DAY, 500.0)]
    frac, _ = align_unlock_to_bars([_iso(5 * _DAY)], events, 1000.0, horizon_days=7)
    assert frac[0] == 0.0  # the day5 unlock is at the bar open → already counted as past


def test_quiet_when_unlocks_far_future_real_zero_not_none() -> None:
    # Events exist but none within any bar's window → genuine 0.0, never None.
    events = [UnlockEvent(100 * _DAY, 500.0)]
    frac, frac_z = align_unlock_to_bars(_days(60), events, 1000.0, horizon_days=7)
    assert all(f == 0.0 for f in frac)  # real zeros
    assert all(z is None for z in frac_z)  # constant series → zero variance → None


def test_zscore_warmup_point_count_then_value() -> None:
    # A single unlock at day50 ramps frac for days 43..49 (horizon 7). With 61 daily
    # bars the z needs UNLOCK_Z_MIN_POINTS points before it is defined.
    events = [UnlockEvent(50 * _DAY, 500.0)]
    frac, frac_z = align_unlock_to_bars(_days(61), events, 1000.0, horizon_days=7)
    assert frac[44] == 0.5  # in the ramp window
    # i = MIN-1 has only MIN points so far counting from 0 → still None below MIN.
    assert frac_z[UNLOCK_Z_MIN_POINTS - 2] is None  # 47 points (< 48)
    # The first bar with >= MIN points AND non-zero dispersion is defined.
    assert frac_z[UNLOCK_Z_MIN_POINTS - 1] is not None  # 48 points, ramp gives variance


def test_unparseable_timestamp_degrades_to_none() -> None:
    events = [UnlockEvent(5 * _DAY, 100.0)]
    frac, frac_z = align_unlock_to_bars(["bar-0", "bar-1"], events, 1000.0)
    assert frac == [None, None]
    assert frac_z == [None, None]


def test_unsorted_events_are_handled() -> None:
    events = [UnlockEvent(10 * _DAY, 300.0), UnlockEvent(5 * _DAY, 100.0)]
    frac, _ = align_unlock_to_bars(_days(4), events, 1000.0, horizon_days=7)
    assert frac[0] == 0.1  # (0,7] sees only the day5 unlock regardless of input order
