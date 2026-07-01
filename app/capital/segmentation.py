"""Capital segmentation accounting (ADR 0013, shadow-only, inert).

Generalises the Lightning treasury 3-account split (``app/lightning/treasury.py``)
to the four capital buckets from the reserve policy — ``operating`` (active
trading), ``reserve`` (risk buffer, out of the risk loop), ``long_term`` (strategic
hold) and ``experiment`` (hard-capped learning / high-risk sandbox). Fiat/USD-aware
(unlike the sats-only treasury), but PURE and read-only: it computes only what IS,
never moves capital. Allocation/transfer is gated at the call site (HOTP +
edge-validation-gate), never here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BUCKETS: tuple[str, ...] = ("operating", "reserve", "long_term", "experiment")

# Legal bucket promotions (recommendation-level only — never executed here).
# Reserve/long-term are deliberately OUT of the risk loop: no path leads back into
# operating/trading, so secured funds can never be silently re-invested.
_ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("operating", "reserve"),
        ("operating", "long_term"),
        ("operating", "experiment"),
        ("reserve", "long_term"),
    }
)

_CAVEAT = (
    "shadow projection — no capital is moved here; allocation/transfer is gated at "
    "the call site (HOTP + edge-validation-gate), never in this function."
)


def compute_segmentation_snapshot(
    balances: Mapping[str, float], *, currency: str = "usd"
) -> dict[str, Any]:
    """Aggregate per-bucket balances into totals + shares (pure, read-only).

    Unknown bucket names or negative balances are rejected (fail-closed). Missing
    buckets are zero-filled so the snapshot always spans the full canonical set.
    """
    by_bucket: dict[str, float] = dict.fromkeys(BUCKETS, 0.0)
    for name, amount in balances.items():
        if name not in by_bucket:
            raise ValueError(f"unknown capital bucket: {name!r} (allowed: {BUCKETS})")
        value = float(amount)
        if value < 0:
            raise ValueError(f"negative balance for bucket {name!r}: {value}")
        by_bucket[name] = value

    total = sum(by_bucket.values())
    shares = {b: (by_bucket[b] / total if total > 0 else 0.0) for b in BUCKETS}

    return {
        "currency": currency,
        "by_bucket": by_bucket,
        "total": total,
        "shares": shares,
        "caveat": _CAVEAT,
    }


def is_allowed_transition(src: str, dst: str) -> bool:
    """True iff moving capital from bucket ``src`` to ``dst`` is a legal promotion.

    Recommendation-level guardrail only — this never moves capital. Reserve and
    long-term never flow back into operating (secured funds stay out of the loop).
    """
    return (src, dst) in _ALLOWED_TRANSITIONS


__all__ = ["BUCKETS", "compute_segmentation_snapshot", "is_allowed_transition"]
