"""asset_performance_score — the pure Mittelweg rotation verdict (no I/O).

G1 of the Momentum-Universe goal. Decides, per asset, whether its recent paper
performance is "keep" (healthy), "rotate-candidate" (weak), or insufficient —
combining two arms so neither alone over-rotates:

- **PnL arm:** net-of-fee realized PnL over the rolling window (> 0 = good).
- **Wilson arm:** the Wilson 95% lower bound of the hit-rate (>= floor = good),
  reusing :func:`app.learning.source_reliability.wilson_lower_bound`.

An asset is **healthy** when it has enough closes AND at least one arm is
positive; it is **weak** ONLY when BOTH arms fail (net PnL <= 0 *and* hit-rate
below floor). Below ``min_closes`` it is insufficient — neither (min-hold
protects fresh assets from premature rotation; the churn doctrine showed cutting
too eagerly bleeds fees). Hysteresis over consecutive weak verdicts lives in the
rotation policy, not here — this module is a pure, deterministic single-shot
verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.learning.source_reliability import wilson_lower_bound

_DEFAULT_MIN_CLOSES = 5
_DEFAULT_WILSON_FLOOR = 0.5


@dataclass(frozen=True)
class AssetWindowStats:
    """Per-asset closed-trade aggregates over the rolling window (caller-supplied)."""

    symbol: str
    net_pnl_usd: float
    closes: int
    wins: int


@dataclass(frozen=True)
class AssetVerdict:
    symbol: str
    closes: int
    sufficient: bool
    net_pnl_usd: float
    pnl_positive: bool
    wilson_lb: float | None
    wilson_ok: bool
    healthy: bool
    weak: bool


def evaluate_asset(
    stats: AssetWindowStats,
    *,
    min_closes: int = _DEFAULT_MIN_CLOSES,
    wilson_floor: float = _DEFAULT_WILSON_FLOOR,
) -> AssetVerdict:
    """Return the single-shot keep/rotate/insufficient verdict for one asset."""
    closes = max(0, int(stats.closes))
    wins = max(0, min(int(stats.wins), closes))
    sufficient = closes >= min_closes

    net = float(stats.net_pnl_usd)
    if not math.isfinite(net):
        net = 0.0
    pnl_positive = net > 0.0

    wilson_lb = wilson_lower_bound(wins, closes) if closes > 0 else None
    wilson_ok = wilson_lb is not None and wilson_lb >= wilson_floor

    healthy = sufficient and (pnl_positive or wilson_ok)
    weak = sufficient and not pnl_positive and not wilson_ok

    return AssetVerdict(
        symbol=stats.symbol,
        closes=closes,
        sufficient=sufficient,
        net_pnl_usd=net,
        pnl_positive=pnl_positive,
        wilson_lb=wilson_lb,
        wilson_ok=wilson_ok,
        healthy=healthy,
        weak=weak,
    )
