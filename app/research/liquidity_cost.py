"""Per-symbol cost realism: venue cost floor + liquidity surcharge from measured turnover.

A flat round-trip cost (e.g. 20bps) is too strict for BTC-class liquidity and far
too lax for thin alts — gates built on it are systematically miscalibrated in both
directions. This module keeps the honest venue-level floor (fees + expected spread
+ slippage from :class:`app.execution.cost_model.CostModel`) and adds a conservative
spread/impact SURCHARGE tiered by the symbol's own measured quote turnover, computed
from the exact OHLCV candles the evaluation already fetched (no extra API calls,
no look-ahead: turnover is a liquidity descriptor, not a signal).

Tiers (daily quote turnover, USD) — deliberately coarse and conservative:

    >= 100M   +0 bps   (BTC/ETH class: venue floor already covers it)
    >=  10M   +10 bps
    >=   3M   +25 bps  (matches the calibrated universe min_turnover floor)
    <    3M   +50 bps  (below the eligibility floor: costs are punitive)

Pure and unit-tested; callers surface the resulting per-symbol map visibly.
"""

from __future__ import annotations

from app.market_data.models import OHLCV

# (min_daily_turnover_usd, surcharge_bps) — first matching tier wins.
TURNOVER_TIERS_BPS: tuple[tuple[float, float], ...] = (
    (100_000_000.0, 0.0),
    (10_000_000.0, 10.0),
    (3_000_000.0, 25.0),
    (0.0, 50.0),
)


def daily_turnover_usd(candles: list[OHLCV], interval_s: int) -> float:
    """Average daily quote turnover (USD) implied by a candle sample.

    ``sum(close * volume)`` over the sample, scaled to a per-day rate by the time
    the sample covers. Robust to any timeframe; ``0.0`` for an empty sample.
    """
    if not candles or interval_s <= 0:
        return 0.0
    quote_turnover = sum(c.close * c.volume for c in candles)
    covered_s = len(candles) * interval_s
    return quote_turnover / covered_s * 86_400.0


def tiered_cost_bps(turnover_usd: float, base_cost_bps: float) -> float:
    """Round-trip cost for one symbol: venue floor + liquidity surcharge."""
    for floor, surcharge in TURNOVER_TIERS_BPS:
        if turnover_usd >= floor:
            return base_cost_bps + surcharge
    return base_cost_bps + TURNOVER_TIERS_BPS[-1][1]


def cost_map_for_series(
    series_by_symbol: dict[str, list[OHLCV]],
    interval_s: int,
    base_cost_bps: float,
) -> dict[str, float]:
    """Per-symbol round-trip cost map from already-fetched OHLCV series."""
    return {
        sym: tiered_cost_bps(daily_turnover_usd(candles, interval_s), base_cost_bps)
        for sym, candles in series_by_symbol.items()
    }


__all__ = [
    "TURNOVER_TIERS_BPS",
    "cost_map_for_series",
    "daily_turnover_usd",
    "tiered_cost_bps",
]
