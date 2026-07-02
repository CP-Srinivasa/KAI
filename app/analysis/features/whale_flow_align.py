"""Causal as-of alignment of sparse whale exchange-flow events onto OHLCV bars.

Whale Alert-style large transfers are sparse, irregular events (a handful per day
per asset), far sparser than 1h research bars. To condition a backtest on whale
flow we map each transfer onto the bar timeline WITHOUT look-ahead:

    a bar opening at time ``t`` may only see transfers CONFIRMED AT OR BEFORE ``t``.

Unlike funding (a level that is forward-filled), flow is an *accumulation*: the
useful per-bar signal is the NET exchange flow over a trailing window ending at
the bar — inflow (coins/stablecoins sent TO an exchange, signed +) minus outflow
(sent FROM an exchange, signed -). ``OHLCV.timestamp_utc`` is the bar OPEN time
(see ``binance_adapter``), so a transfer confirmed before the bar opens is known
by the time we would act at that bar; the strict ``event_ms <= bar_open_ms`` rule
is the conservative causal choice (asserted in ``tests/unit/test_whale_flow_align``).

Two causal features come out per bar:
  * ``netflow`` — net signed USD flow in the trailing ``window_ms`` ending at the
    bar (0.0 is a real value: "no whale flow lately", not missing);
  * ``netflow_z`` — a rolling z-score of that netflow series over the last
    ``z_window`` bars known as of the bar (flow regimes drift; a self-relative z is
    far more robust than an absolute USD threshold). NOTE: consecutive trailing
    sums overlap heavily, so the netflow series is strongly autocorrelated — the z
    is a normalised *signal*, not an i.i.d. statistic; downstream inference
    (BH-FDR, block-bootstrap) accounts for that.

Pure module: no I/O, no network. Building ``FlowPoint``s from a raw source lives
in ``scripts/build_whale_flow_series.py``; this only aligns what it is given.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

# Trailing accumulation window for the per-bar net flow. Whale flows act on a
# 1h–24h horizon (academic evidence), so a 24h trailing net is the natural signal.
FLOW_WINDOW_MS = 24 * 60 * 60 * 1000
# Rolling window (number of bars) for the netflow z-score: ~7 days at 1h bars —
# long enough to characterise the current flow regime, short enough to react.
FLOW_Z_WINDOW = 168
# Minimum defined points in the window before a z is emitted; below this the z is
# None (decider treats None as "no trade"), never a fabricated 0.
FLOW_Z_MIN_POINTS = 24


@dataclass(frozen=True)
class FlowPoint:
    """One whale exchange-flow event.

    ``event_ms`` is the transfer confirmation time (epoch ms, UTC). ``signed_usd``
    is +value_usd for an inflow (TO an exchange) and -value_usd for an outflow
    (FROM an exchange); exchange-internal and non-exchange transfers are excluded
    upstream and never appear here.
    """

    event_ms: int
    signed_usd: float


def _iso_to_ms(ts: str) -> int | None:
    """Parse an ISO-8601 bar timestamp to epoch ms, forcing UTC for naive inputs.

    Returns None for anything that does not parse (e.g. synthetic ``"bar-3"``
    labels) so alignment degrades to "no flow" rather than raising.
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

    None when the window has fewer than ``FLOW_Z_MIN_POINTS`` points or zero
    dispersion (a flat flow regime has no meaningful extreme).
    """
    if len(window) < FLOW_Z_MIN_POINTS:
        return None
    mean = statistics.fmean(window)
    std = statistics.pstdev(window)
    if std <= 0.0:
        return None
    return (value - mean) / std


def align_flow_to_bars(
    bar_timestamps_utc: Sequence[str],
    flows: Sequence[FlowPoint],
    *,
    window_ms: int = FLOW_WINDOW_MS,
    z_window: int = FLOW_Z_WINDOW,
) -> tuple[list[float | None], list[float | None]]:
    """Accumulate whale flow into a trailing per-bar netflow and a rolling z.

    Args:
        bar_timestamps_utc: oldest-first bar OPEN timestamps (ISO-8601). Assumed
            monotonically non-decreasing (as Binance klines are).
        flows: whale flow events (any order; sorted internally).
        window_ms: trailing accumulation window in ms (default 24h).
        z_window: number of recent bars used for the netflow z-score.

    Returns:
        ``(netflow, netflow_z)`` each aligned 1:1 to ``bar_timestamps_utc``. For a
        bar that precedes the first flow event (or whose timestamp does not parse)
        both are None — there is no flow information yet, distinct from a genuine
        zero net once events exist. ``netflow_z`` is None until ``FLOW_Z_MIN_POINTS``
        netflow points are known.
    """
    n = len(bar_timestamps_utc)
    netflow: list[float | None] = [None] * n
    netflow_z: list[float | None] = [None] * n
    if not flows or n == 0:
        return netflow, netflow_z

    pts = sorted(flows, key=lambda p: p.event_ms)
    ev_ms = [p.event_ms for p in pts]
    ev_usd = [p.signed_usd for p in pts]
    m = len(pts)
    first_ev = ev_ms[0]

    hi = 0  # first event index NOT yet inside (<= bar): events [lo, hi) are in-window
    lo = 0  # first event index still inside the trailing window
    running = 0.0
    z_hist: list[float] = []
    for i, ts in enumerate(bar_timestamps_utc):
        bar_ms = _iso_to_ms(ts)
        if bar_ms is None:
            continue
        if bar_ms < first_ev:
            continue  # bar precedes any known whale flow → no information
        # Admit events confirmed at or before this bar (causal).
        while hi < m and ev_ms[hi] <= bar_ms:
            running += ev_usd[hi]
            hi += 1
        # Evict events that fell out of the trailing window.
        lower = bar_ms - window_ms
        while lo < hi and ev_ms[lo] <= lower:
            running -= ev_usd[lo]
            lo += 1
        netflow[i] = running
        z_hist.append(running)
        window = z_hist[-z_window:]
        netflow_z[i] = _rolling_z(window, running)
    return netflow, netflow_z
