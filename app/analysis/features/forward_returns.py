"""Forward-return labeling — pure function.

The label for index ``i`` is the gross close-to-close market move over a forward
horizon, expressed in basis points:

    fwd_bps[i] = 10000 * (close[i + horizon] / close[i] - 1)

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
        if base <= 0 or future <= 0:
            continue
        out[i] = _BPS * (future / base - 1.0)
    return out
