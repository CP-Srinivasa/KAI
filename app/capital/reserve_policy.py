"""Reserve/profit-split recommendation (ADR 0013, shadow-only, inert).

On a realized gain, recommends how much to move out of the risk loop into the
reserve (capped at its target) with the overflow rolling to long-term hold. PURE:
it returns numbers and ``executes=False``; it never moves capital. Actual movement
is gated at the call site (HOTP + edge-validation-gate).
"""

from __future__ import annotations

from typing import Any


def compute_reserve_recommendation(
    realized_gain_usd: float,
    *,
    current_reserve_usd: float,
    profit_split_pct: float,
    reserve_target_usd: float,
) -> dict[str, Any]:
    """Recommend the reserve/long-term split for a realized gain (pure, no execute).

    A non-positive gain yields a zero recommendation. ``profit_split_pct`` must be a
    fraction in ``[0, 1]``. Reserve is filled up to ``reserve_target_usd``; the
    remaining split rolls to long-term hold.
    """
    if not 0.0 <= profit_split_pct <= 1.0:
        raise ValueError(f"profit_split_pct must be in [0, 1], got {profit_split_pct}")

    gain = max(0.0, float(realized_gain_usd))
    split = gain * profit_split_pct
    room_in_reserve = max(0.0, float(reserve_target_usd) - float(current_reserve_usd))
    to_reserve = min(split, room_in_reserve)
    to_long_term = split - to_reserve
    keep_operating = gain - split

    return {
        "to_reserve_usd": to_reserve,
        "to_long_term_usd": to_long_term,
        "keep_operating_usd": keep_operating,
        "executes": False,  # recommendation only — movement is gated at the call site
        "rationale": (
            f"split {profit_split_pct:.0%} of realized gain; reserve filled to target "
            "then overflow to long-term hold."
        ),
    }


__all__ = ["compute_reserve_recommendation"]
