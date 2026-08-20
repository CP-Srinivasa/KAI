"""Forward-return labeling — pure functions.

Two conventions live here, deliberately side by side.

**Close-to-close** (``compute_forward_return_bps``) is the research convention
the twelve sealed TA verdicts were measured under. It stays untouched; changing
it would silently invalidate them.

    fwd_bps[i] = 10000 * (close[i + horizon] / close[i] - 1)

**Next-open** (``compute_next_open_forward_return_bps``) is the executable
convention, required for any confirmatory signal that might later be automated.
RSI and volume of bar ``t`` are only known once ``t`` has closed — so one cannot
simultaneously claim to have entered at ``close(t)``. Entry is the OPEN of the
following bar.

This is intentionally FORWARD-looking: the label at index i uses the FUTURE bar
``i + horizon``. That is correct and necessary for a supervised label. The
no-look-ahead rule applies to FEATURES (see ``feature_matrix``), never to the
label — and the two must never be confused: a label must not be fed back as a
feature.

Costs are deliberately NOT included here. The label is raw market truth; the
cost/slippage overlay belongs to hypothesis evaluation (CostModel), so the same
labels can be reused across cost assumptions.

Output is aligned to input length. The last ``horizon`` positions are None (no
future bar). Non-positive prices (base or future) yield None at that position to
keep the ratio well-defined.
"""

from __future__ import annotations

import math

_BPS = 10_000.0


def compute_forward_return_bps(closes: list[float], horizon: int) -> list[float | None]:
    """Compute forward close-to-close returns in basis points, aligned to ``closes``.

    Args:
        closes: ordered close prices (oldest first).
        horizon: number of bars to look forward. Must be >= 1.

    Returns:
        list with len(closes) entries. float bps where a future bar exists and
        both prices are positive; None for the trailing ``horizon`` positions
        and wherever a price is non-positive.

    Raises:
        ValueError: horizon < 1.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(n - horizon):
        base = closes[i]
        future = closes[i + horizon]
        if base <= 0 or future <= 0 or not math.isfinite(base) or not math.isfinite(future):
            continue
        out[i] = _BPS * (future / base - 1.0)
    return out


def compute_next_open_forward_return_bps(
    opens: list[float],
    closes: list[float],
    horizon: int,
) -> list[float | None]:
    """Executable forward return: enter at the NEXT bar's open, exit at close.

    The index semantics are written out rather than left to the formula, because
    two equally plausible readings differ by a full candle::

        SIGNAL_BAR_INDEX = t                        # signal fixed with close(t)
        ENTRY_BAR_INDEX  = t + 1                    # entry at this bar's OPEN
        HOLDING_BARS     = h
        EXIT_BAR_INDEX   = ENTRY_BAR_INDEX + h - 1  = t + h

        label_bps[t] = 10000 * ( close[t + h] / open[t + 1] - 1 )

    Worked example (h = 4, hourly bars), pinned by a golden test at concrete
    timestamps rather than by this docstring:

        signal 12:00-12:59  ->  entry OPEN 13:00  ->  exit CLOSE 16:59

    The tempting misreading is ``EXIT = t + 1 + h`` (close 17:59) — one hour too
    late, and it would quietly inflate or deflate every measured edge.

    ``open`` deliberately does NOT live in :class:`FeatureRow`. Features are what
    is known at signal time; the entry open lies *after* the decision. Exposing
    it to a decider would soften the matrix's no-look-ahead boundary.

    Args:
        opens: ordered bar opens (oldest first), aligned with ``closes``.
        closes: ordered bar closes (oldest first).
        horizon: holding period in bars. Must be >= 1.

    Returns:
        One entry per input bar. float bps where both the entry bar and the exit
        bar exist with positive finite prices; None for the trailing positions
        and wherever a price is unusable.

    Raises:
        ValueError: horizon < 1, or opens and closes differ in length.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if len(opens) != len(closes):
        raise ValueError("opens and closes must have equal length")
    n = len(closes)
    out: list[float | None] = [None] * n
    for t in range(n - horizon):
        entry = opens[t + 1]
        exit_price = closes[t + horizon]
        if entry <= 0 or exit_price <= 0:
            continue
        if not math.isfinite(entry) or not math.isfinite(exit_price):
            continue
        out[t] = _BPS * (exit_price / entry - 1.0)
    return out
