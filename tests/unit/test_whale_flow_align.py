"""Causal whale-flow-to-bar alignment tests.

The real risks of mapping sparse whale transfers onto dense bars:
  1. CAUSALITY: a bar must never see a transfer confirmed AFTER it.
  2. Trailing accumulation: events sum within the window and are evicted when old.
  3. Net signing: inflow (+) and outflow (-) net per bar.
  4. Z-score correctness + None semantics (warm-up / zero variance).
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime

from app.analysis.features.whale_flow_align import (
    FLOW_Z_MIN_POINTS,
    FlowPoint,
    align_flow_to_bars,
)

_H = 3_600_000  # 1h in ms


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def test_empty_flows_yield_all_none() -> None:
    bars = [_iso(i * _H) for i in range(5)]
    netflow, netflow_z = align_flow_to_bars(bars, [])
    assert netflow == [None] * 5
    assert netflow_z == [None] * 5


def test_inflow_outflow_net_within_window() -> None:
    # A +100k inflow and a -30k outflow both confirmed before the bar → net +70k.
    flows = [FlowPoint(0, 100_000.0), FlowPoint(_H // 2, -30_000.0)]
    bars = [_iso(_H)]
    netflow, _ = align_flow_to_bars(bars, flows, window_ms=24 * _H)
    assert netflow[0] == 70_000.0


def test_trailing_window_evicts_old_events() -> None:
    # window = 1h. Event at t=0 (+100k) and t=2h (+50k). At bar t=2h only the
    # t=2h event is still in the trailing (1h) window → 50k (the t=0 event aged out).
    flows = [FlowPoint(0, 100_000.0), FlowPoint(2 * _H, 50_000.0)]
    netflow, _ = align_flow_to_bars([_iso(0), _iso(2 * _H)], flows, window_ms=_H)
    assert netflow[0] == 100_000.0  # bar t=0 sees the t=0 inflow
    assert netflow[1] == 50_000.0  # bar t=2h: t=0 event evicted, only t=2h in window


def test_no_lookahead_event_after_bar_is_invisible() -> None:
    # A transfer confirmed exactly at the bar open IS visible; one strictly after
    # is NOT (and, being the only/first event, leaves the bar with no info → None).
    bars = [_iso(8 * _H)]
    at_open = align_flow_to_bars(bars, [FlowPoint(8 * _H, 5_000.0)], window_ms=24 * _H)[0]
    after = align_flow_to_bars(bars, [FlowPoint(8 * _H + 1, 5_000.0)], window_ms=24 * _H)[0]
    assert at_open[0] == 5_000.0  # confirmed at-or-before open → visible
    assert after[0] is None  # confirmed after the bar opened → look-ahead, hidden


def test_bar_before_first_event_is_none_then_zero_is_real() -> None:
    # First flow at t=2h; bars at t=0,1h precede it → None (no info). Once events
    # exist, a bar whose trailing window holds nothing is a genuine 0.0, not None.
    flows = [FlowPoint(2 * _H, 10_000.0)]
    bars = [_iso(0), _iso(_H), _iso(2 * _H), _iso(4 * _H)]
    netflow, _ = align_flow_to_bars(bars, flows, window_ms=_H)
    assert netflow[0] is None and netflow[1] is None
    assert netflow[2] == 10_000.0
    assert netflow[3] == 0.0  # event aged out of the 1h window → real zero flow


def test_zscore_warmup_then_spike() -> None:
    # window = 1h with one event per hour → each bar's netflow == that event's value
    # (clean control). A calm regime then a clear inflow spike.
    vals = [100.0] * (FLOW_Z_MIN_POINTS + 5) + [50_000.0]
    flows = [FlowPoint(i * _H, v) for i, v in enumerate(vals)]
    bars = [_iso(i * _H) for i in range(len(vals))]
    netflow, netflow_z = align_flow_to_bars(bars, flows, window_ms=_H)
    assert netflow == vals  # one-event-per-window control holds
    # First (FLOW_Z_MIN_POINTS - 1) bars: insufficient window → None.
    for i in range(FLOW_Z_MIN_POINTS - 1):
        assert netflow_z[i] is None
    last = len(vals) - 1
    expected = (vals[last] - statistics.fmean(vals)) / statistics.pstdev(vals)
    assert netflow_z[last] is not None
    assert math.isclose(netflow_z[last], expected, rel_tol=1e-9)
    assert netflow_z[last] > 1.0  # the spike crosses the decider trigger


def test_zscore_zero_variance_is_none() -> None:
    # Constant netflow regime → no dispersion → z undefined (not a fake 0).
    vals = [100.0] * (FLOW_Z_MIN_POINTS + 3)
    flows = [FlowPoint(i * _H, v) for i, v in enumerate(vals)]
    bars = [_iso(i * _H) for i in range(len(vals))]
    _, netflow_z = align_flow_to_bars(bars, flows, window_ms=_H)
    assert all(z is None for z in netflow_z)


def test_unsorted_flows_are_handled() -> None:
    # Input order must not matter — the function sorts by event time.
    flows = [FlowPoint(2 * _H, 50_000.0), FlowPoint(0, 100_000.0)]
    netflow, _ = align_flow_to_bars([_iso(0), _iso(2 * _H)], flows, window_ms=_H)
    assert netflow == [100_000.0, 50_000.0]


def test_unparseable_timestamp_degrades_to_none() -> None:
    flows = [FlowPoint(0, 100_000.0)]
    netflow, netflow_z = align_flow_to_bars(["bar-0", "bar-1"], flows)
    assert netflow == [None, None]
    assert netflow_z == [None, None]
