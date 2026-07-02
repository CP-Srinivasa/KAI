"""Shared phantom-close detection (DS-20260529-V1).

A paper close whose implied per-trade return exceeds a sanity cap is the
signature of a price-source disagreement (entry and exit priced by different
providers) — e.g. BitMEX's delisted "MATIC" instrument at 0.40875 vs the real
~0.088, which booked +364% per cycle. The paper engine refuses to book such
closes going forward (close_price_sanity_rejected); this module lets read-side
aggregators (realized-by-asset, paper-quality) exclude the historical phantom
closes that were booked before the guard existed, so dashboards show the real
PnL instead of the phantom profit.

The threshold mirrors the engine's MAX_CLOSE_RETURN_PCT so detection is
consistent across the write path (rejection) and the read path (exclusion).
"""

from __future__ import annotations

import os

_DEFAULT_MAX_CLOSE_RETURN_PCT = 2.0


def phantom_return_threshold() -> float:
    """Implied per-trade return magnitude (fraction) above which a close is phantom."""
    raw = os.environ.get("MAX_CLOSE_RETURN_PCT")
    if raw is None:
        return _DEFAULT_MAX_CLOSE_RETURN_PCT
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_MAX_CLOSE_RETURN_PCT
    return value if value > 0 else _DEFAULT_MAX_CLOSE_RETURN_PCT


def implied_close_return(entry_price: float, exit_price: float, position_side: str) -> float | None:
    """Signed per-trade return of closing at ``exit_price``. None if prices non-positive."""
    if entry_price <= 0 or exit_price <= 0:
        return None
    if position_side == "short":
        return entry_price / exit_price - 1.0
    return exit_price / entry_price - 1.0


def is_phantom_close(
    entry_price: object,
    exit_price: object,
    position_side: object,
    *,
    threshold: float | None = None,
) -> bool:
    """True when a close's implied return magnitude exceeds the phantom threshold.

    Conservative: returns False when entry/exit are missing or non-numeric — an
    unverifiable close is never silently dropped from realized PnL.
    """
    if not isinstance(entry_price, (int, float)) or not isinstance(exit_price, (int, float)):
        return False
    side = position_side if isinstance(position_side, str) else "long"
    r = implied_close_return(float(entry_price), float(exit_price), side)
    if r is None:
        return False
    cap = threshold if threshold is not None else phantom_return_threshold()
    return abs(r) > cap
