"""Venue trust SSOT for cross-exchange validation (Issue #169, item 2).

The cross-exchange weighted-median (PR #168) needs a per-venue
``exchange_trust_score`` in ``[0, 1]``. This module is the single source of truth
for that score.

Posture: **static venue reputation** for now (deterministic, auditable, no
network). A future rolling-reliability source can replace the table behind the
same ``venue_trust_score(...)`` accessor without touching call-sites — but the
static table is the SSOT until that lands (Issue #169 leaves the rolling source
to calibration).

Fail-closed: an unknown venue resolves to :data:`UNKNOWN_VENUE_TRUST` (a
conservative low score), never a high default — an unrecognised feed must not be
trusted into the median by accident.
"""

from __future__ import annotations

# Conservative score for any venue not in the table. Low enough that an unknown
# feed is heavily down-weighted but not zero (it can still contribute if it is
# the only quote — the median layer handles single-provider degeneracy).
UNKNOWN_VENUE_TRUST = 0.3

# Static venue reputation. Tier rationale:
#   0.95 — top-liquidity regulated/major derivatives venues
#   0.90 — major spot/derivatives venues
#   0.80 — solid mid-tier venues
#   0.70 — aggregators (price is derived, not a single matched book)
# Keys are the canonical adapter source ids used across app/market_data.
_VENUE_TRUST: dict[str, float] = {
    "binance_futures": 0.95,
    "binance": 0.95,
    "bybit": 0.90,
    "okx": 0.90,
    "bitmex": 0.85,
    "bitget": 0.80,
    "kucoin": 0.80,
    "huobi": 0.78,
    "bingx": 0.75,
    # Aggregator / index feeds — a derived price, not a single venue's book.
    "coingecko": 0.70,
    "coinbase": 0.90,
}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def venue_trust_score(provider_id: str | None) -> float:
    """Return the trust score in ``[0, 1]`` for ``provider_id`` (fail-closed).

    Case-insensitive on the canonical id. Unknown/blank → :data:`UNKNOWN_VENUE_TRUST`.
    """
    if not provider_id:
        return UNKNOWN_VENUE_TRUST
    key = provider_id.strip().lower()
    return _clamp01(_VENUE_TRUST.get(key, UNKNOWN_VENUE_TRUST))


def known_venues() -> tuple[str, ...]:
    """All venue ids with an explicit (non-default) trust score."""
    return tuple(sorted(_VENUE_TRUST))


__all__ = [
    "UNKNOWN_VENUE_TRUST",
    "known_venues",
    "venue_trust_score",
]
