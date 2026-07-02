"""MarketDataPoint → ProviderQuote mapping (Issue #169, item 1).

``validate_cross_exchange`` (PR #168) consumes :class:`ProviderQuote` — a
microstructure snapshot (price/bid/ask/volume/orderbook_depth/timestamp/trust/
latency). Today's adapters emit only :class:`MarketDataPoint`
(price/volume_24h/change/source/is_stale/freshness) and carry **no** bid/ask/
depth. This module maps what we have into a quote, supplied with the
microstructure fields when an adapter can provide them.

Honesty contract
----------------
- The trust score comes from the venue-trust SSOT
  (:func:`app.market_data.venue_trust.venue_trust_score`), never invented.
- ``timestamp_ms`` is derived from the point's ISO timestamp, or — when that is
  absent/unparseable — from ``now_ms - freshness_seconds*1000`` (the freshness
  the adapter reported). Never fabricated as "now".
- **No faked microstructure.** ``bid``/``ask``/``orderbook_depth`` must be
  supplied (an adapter that cannot produce them yields ``None`` here →
  the venue is *excluded* from the median, not included with a fake zero-spread
  full-credit quote). This keeps the weighted median honest until the adapters
  grow real bid/ask/depth (tracked in Issue #169 as the adapter-network step).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.market_data.cross_exchange import ProviderQuote
from app.market_data.models import MarketDataPoint
from app.market_data.venue_trust import venue_trust_score


@dataclass(frozen=True)
class Microstructure:
    """Per-venue microstructure an adapter supplies alongside a price.

    ``orderbook_depth`` is aggregated near-touch depth in quote currency;
    ``latency_ms`` is the request round-trip the adapter measured (0.0 unknown).
    """

    bid: float
    ask: float
    orderbook_depth: float
    latency_ms: float = 0.0


def _timestamp_ms(point: MarketDataPoint, *, now_ms: float) -> float:
    """Resolve the quote's epoch-ms timestamp from the point, fail-soft."""
    raw = (point.timestamp_utc or "").strip()
    if raw:
        iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            return datetime.fromisoformat(iso).timestamp() * 1000.0
        except ValueError:
            pass
    # Fall back to the reported freshness (age in seconds) relative to now.
    return now_ms - max(point.freshness_seconds, 0.0) * 1000.0


def build_provider_quote(
    point: MarketDataPoint,
    microstructure: Microstructure | None,
    *,
    now_ms: float,
) -> ProviderQuote | None:
    """Map a :class:`MarketDataPoint` (+ microstructure) into a
    :class:`ProviderQuote`, or ``None`` when the venue lacks the microstructure
    needed for an honest cross-exchange quote.

    Excluded (returns ``None``) when: price is non-positive, or microstructure is
    missing, or bid/ask are not a valid positive ``bid <= ask`` pair.
    """
    if point.price <= 0:
        return None
    if microstructure is None:
        return None
    bid, ask = microstructure.bid, microstructure.ask
    if bid <= 0 or ask <= 0 or bid > ask:
        return None
    return ProviderQuote(
        provider_id=point.source,
        price=point.price,
        bid=bid,
        ask=ask,
        volume=max(point.volume_24h, 0.0),
        orderbook_depth=max(microstructure.orderbook_depth, 0.0),
        timestamp_ms=_timestamp_ms(point, now_ms=now_ms),
        exchange_trust_score=venue_trust_score(point.source),
        latency_ms=max(microstructure.latency_ms, 0.0),
    )


__all__ = [
    "Microstructure",
    "build_provider_quote",
]
