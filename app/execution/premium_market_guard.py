"""Premium market-data guards for bridge scale/entry decisions.

The bridge only needs a narrow, conservative filter: one impossible quote must
not be allowed to resolve scale, validate geometry, fill an entry, or terminally
reject a waiting signal. This module stays pure so tests can replay bad ticks
without touching live market data.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

PRICE_OUTLIER_EVENT = "premium_market_price_outlier_rejected"
BAD_TICK_IGNORED_EVENT = "premium_bad_tick_ignored"
TERMINAL_STABILIZED_EVENT = "premium_terminal_stabilized"
REQUIRES_QUOTE_SOURCE_EVENT = "premium_requires_quote_source"
SCALE_UNRESOLVED_EVENT = "premium_scale_unresolved_or_bad_price"
SCALE_RESOLVED_EVENT = "premium_scale_resolved_persisted"

OUTLIER_MAX_DEVIATION_PCT = 50.0
BAD_TICK_TERMINAL_THRESHOLD = 3
EXTREME_UNRESOLVED_RATIO = 100.0

_BAD_TICK_EVENTS = frozenset(
    {
        PRICE_OUTLIER_EVENT,
        BAD_TICK_IGNORED_EVENT,
    }
)


@dataclass(frozen=True)
class PriceOutlierDecision:
    accepted: bool
    reason: str | None = None
    reference_price: float | None = None
    deviation_pct: float | None = None
    reference_source: str | None = None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
        return out if out > 0 else None
    return None


def _matches_signal(
    rec: dict[str, Any],
    *,
    envelope_id: str,
    correlation_id: str,
    symbol: str,
) -> bool:
    env = rec.get("envelope_id")
    corr = rec.get("correlation_id")
    if env != envelope_id and corr != correlation_id:
        return False
    rec_symbol = rec.get("symbol")
    if isinstance(rec_symbol, str) and rec_symbol and rec_symbol != symbol:
        return False
    return True


def _record_events(rec: dict[str, Any]) -> set[str]:
    events: set[str] = set()
    event = rec.get("event")
    if isinstance(event, str):
        events.add(event)
    secondary = rec.get("secondary_event")
    if isinstance(secondary, str):
        events.add(secondary)
    audit_events = rec.get("audit_events")
    if isinstance(audit_events, list):
        events.update(str(e) for e in audit_events if e is not None)
    return events


def latest_valid_spot(
    records: list[dict[str, Any]],
    *,
    envelope_id: str,
    correlation_id: str,
    symbol: str,
) -> float | None:
    """Return the most recent non-bad current_price for this signal."""
    for rec in reversed(records):
        if not _matches_signal(
            rec,
            envelope_id=envelope_id,
            correlation_id=correlation_id,
            symbol=symbol,
        ):
            continue
        if _record_events(rec) & _BAD_TICK_EVENTS:
            continue
        price = _safe_float(rec.get("current_price")) or _safe_float(rec.get("fill_price"))
        if price is not None:
            return price
    return None


def consecutive_bad_ticks(
    records: list[dict[str, Any]],
    *,
    envelope_id: str,
    correlation_id: str,
    symbol: str,
) -> int:
    """Count trailing bad-tick records for this signal."""
    count = 0
    for rec in reversed(records):
        if not _matches_signal(
            rec,
            envelope_id=envelope_id,
            correlation_id=correlation_id,
            symbol=symbol,
        ):
            continue
        events = _record_events(rec)
        if events & _BAD_TICK_EVENTS:
            count += 1
            continue
        if _safe_float(rec.get("current_price")) is not None:
            break
    return count


def validate_spot_price(
    current_price: float,
    *,
    previous_valid_price: float | None = None,
    provider_prices: list[float] | None = None,
    max_deviation_pct: float = OUTLIER_MAX_DEVIATION_PCT,
) -> PriceOutlierDecision:
    """Reject a spot that is far away from prior valid price or provider median."""
    if current_price <= 0:
        return PriceOutlierDecision(False, reason="non_positive_price")

    references: list[tuple[str, float]] = []
    if previous_valid_price is not None and previous_valid_price > 0:
        references.append(("last_valid_spot", previous_valid_price))
    provider_refs = [p for p in (provider_prices or []) if p > 0]
    if provider_refs:
        references.append(("cross_provider_median", statistics.median(provider_refs)))

    for source, reference in references:
        deviation = abs(current_price - reference) / reference * 100.0
        if deviation > max_deviation_pct:
            return PriceOutlierDecision(
                False,
                reason="price_deviation_exceeds_guard",
                reference_price=reference,
                deviation_pct=deviation,
                reference_source=source,
            )
    return PriceOutlierDecision(True)


def scale_unresolved_or_bad_price(
    *,
    entry: float,
    spot: float,
    scale_factor: float,
    extreme_ratio: float = EXTREME_UNRESOLVED_RATIO,
) -> bool:
    """True when pass-through scale would feed an obviously impossible plan."""
    if entry <= 0 or spot <= 0 or scale_factor != 1.0:
        return False
    ratio = entry / spot
    return ratio > extreme_ratio or ratio < (1.0 / extreme_ratio)


__all__ = [
    "BAD_TICK_IGNORED_EVENT",
    "BAD_TICK_TERMINAL_THRESHOLD",
    "PRICE_OUTLIER_EVENT",
    "REQUIRES_QUOTE_SOURCE_EVENT",
    "SCALE_RESOLVED_EVENT",
    "SCALE_UNRESOLVED_EVENT",
    "TERMINAL_STABILIZED_EVENT",
    "PriceOutlierDecision",
    "consecutive_bad_ticks",
    "latest_valid_spot",
    "scale_unresolved_or_bad_price",
    "validate_spot_price",
]
