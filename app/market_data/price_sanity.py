"""Per-symbol price-outlier gate for the premium read path (BUG-2, 2026-06-08).

Root cause SKYAI 2026-06-07: the market-data fallback chain returned a garbage
single-provider spot (0.35609 → unavailable → 0.35561 → **101.94**) for a
low-cap symbol. The 286× jump was not cross-checkable (single provider) so the
existing DS-20260529-V1 provider-disagreement guard could not catch it. That
garbage spot then drove scale detection to fail-soft 1.0, kept the raw 24800
entry, and produced a misleading ``long_sl_at_or_above_spot`` reject.

This module is a **pure, stateful-by-injection** sanity gate that runs in the
read path BEFORE a spot is used for scale/validation/PnL. A candidate price is
rejected (reason ``premium_market_price_outlier_rejected``) when it deviates
from the last known-good spot for that symbol by more than a physically
implausible ratio, or — when available — from the cross-provider median.

Design notes:
- Pure verdict function ``evaluate_price_sanity`` is the unit-test seam; it has
  no I/O and no global state.
- ``LastGoodPriceStore`` is a tiny in-memory last-good cache, injectable for
  tests; production uses a module-level singleton via ``get_last_good_store``.
- First-ever price for a symbol (no last_good, no median) is accepted
  best-effort but tagged ``verified=False`` so callers can require corroboration
  for risk-increasing actions if they choose.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# A spot that jumps by more than this *ratio* (either direction) versus the last
# known-good spot of the SAME symbol between adjacent read ticks is treated as a
# wrong-instrument / feed glitch, not a real move. Crypto can be volatile, but a
# 5× move tick-to-tick (seconds apart) is a data error, not a market event. The
# SKYAI case was 286×. Env-tunable via PREMIUM_PRICE_OUTLIER_MAX_RATIO.
_DEFAULT_MAX_RATIO = 5.0

# When a cross-provider median is available, a candidate that deviates from the
# median by more than this fraction is rejected even on the first tick. Spot/
# futures venues agree to well within this on any real pair.
_DEFAULT_MEDIAN_TOLERANCE_PCT = 0.35

OUTLIER_REASON = "premium_market_price_outlier_rejected"


def _max_ratio() -> float:
    raw = os.environ.get("PREMIUM_PRICE_OUTLIER_MAX_RATIO")
    if raw is None:
        return _DEFAULT_MAX_RATIO
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_MAX_RATIO
    return value if value > 1.0 else _DEFAULT_MAX_RATIO


def _median_tolerance_pct() -> float:
    raw = os.environ.get("PREMIUM_PRICE_OUTLIER_MEDIAN_PCT")
    if raw is None:
        return _DEFAULT_MEDIAN_TOLERANCE_PCT
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_MEDIAN_TOLERANCE_PCT
    return value if value > 0 else _DEFAULT_MEDIAN_TOLERANCE_PCT


@dataclass(frozen=True)
class PriceSanityVerdict:
    """Result of a price-sanity evaluation.

    ``ok`` False means the candidate must NOT be used for scale/validation/PnL.
    ``outlier_score`` is the max observed deviation ratio (>=1.0 for the
    last-good check, or the median deviation fraction) for audit/logging.
    ``verified`` True means the candidate was corroborated (within band of
    last-good or median); False means accepted best-effort without corroboration
    (first-ever tick, no reference).
    """

    ok: bool
    reason: str | None
    outlier_score: float
    verified: bool
    reference: str  # "last_good" | "median" | "none"


def evaluate_price_sanity(
    *,
    symbol: str,
    candidate_price: float | None,
    last_good_price: float | None = None,
    median_price: float | None = None,
    max_ratio: float | None = None,
    median_tolerance_pct: float | None = None,
) -> PriceSanityVerdict:
    """Decide whether ``candidate_price`` is a usable spot for ``symbol``.

    Order of checks:
    1. Non-positive / missing candidate -> not ok (treated as no-data upstream).
    2. Cross-provider median available -> reject if deviation > tolerance.
    3. Last-good available -> reject if ratio outside [1/max_ratio, max_ratio].
    4. No reference at all -> accept best-effort, verified=False.
    """
    ratio_cap = max_ratio if max_ratio is not None else _max_ratio()
    med_tol = median_tolerance_pct if median_tolerance_pct is not None else _median_tolerance_pct()

    if candidate_price is None or candidate_price <= 0:
        return PriceSanityVerdict(
            ok=False, reason="no_price", outlier_score=0.0, verified=False, reference="none"
        )

    # 2. Median check (strongest signal when present).
    if median_price is not None and median_price > 0:
        dev = abs(candidate_price - median_price) / median_price
        if dev > med_tol:
            return PriceSanityVerdict(
                ok=False,
                reason=OUTLIER_REASON,
                outlier_score=round(dev, 6),
                verified=False,
                reference="median",
            )
        return PriceSanityVerdict(
            ok=True, reason=None, outlier_score=round(dev, 6), verified=True, reference="median"
        )

    # 3. Last-good ratio band.
    if last_good_price is not None and last_good_price > 0:
        ratio = candidate_price / last_good_price
        score = max(ratio, 1.0 / ratio)
        if ratio > ratio_cap or ratio < (1.0 / ratio_cap):
            return PriceSanityVerdict(
                ok=False,
                reason=OUTLIER_REASON,
                outlier_score=round(score, 6),
                verified=False,
                reference="last_good",
            )
        return PriceSanityVerdict(
            ok=True,
            reason=None,
            outlier_score=round(score, 6),
            verified=True,
            reference="last_good",
        )

    # 4. No reference — accept best-effort, mark unverified.
    return PriceSanityVerdict(
        ok=True, reason=None, outlier_score=1.0, verified=False, reference="none"
    )


class LastGoodPriceStore:
    """Tiny in-memory last-good spot cache, keyed by symbol.

    Only verified-good prices are recorded, so a rejected garbage tick never
    becomes the reference that a later (real) tick is judged against.
    """

    def __init__(self) -> None:
        self._prices: dict[str, float] = {}

    def get(self, symbol: str) -> float | None:
        return self._prices.get(symbol.upper())

    def record(self, symbol: str, price: float) -> None:
        if price > 0:
            self._prices[symbol.upper()] = float(price)

    def clear(self) -> None:
        self._prices.clear()


_STORE: LastGoodPriceStore | None = None


def get_last_good_store() -> LastGoodPriceStore:
    global _STORE
    if _STORE is None:
        _STORE = LastGoodPriceStore()
    return _STORE


__all__ = [
    "OUTLIER_REASON",
    "LastGoodPriceStore",
    "PriceSanityVerdict",
    "evaluate_price_sanity",
    "get_last_good_store",
]
