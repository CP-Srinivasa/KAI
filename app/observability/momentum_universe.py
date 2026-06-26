"""momentum_universe — pure universe ranking (no I/O).

Combines a "most traded" signal (24h quote turnover) with a multi-window
price-momentum signal into a single, bounded Universe-Score and returns the
top-N symbols ranked. PURE by design: callers fetch the raw inputs themselves
(turnover via ``adapter.top_symbols_by_volume`` + ticker ``volume_24h``; window
returns computed from OHLCV closes) and pass them in. This module only decides
the ranking — deterministically — and is fully unit-testable without network or
disk.

Robustness doctrine (matches the project's edge/quality line):
- Both signals are normalized to a **percentile rank within the candidate set**
  (the fraction of the field each value strictly beats), so wildly different
  scales (turnover in millions vs. % returns) combine sanely and a single
  outlier cannot dominate — it caps at 1.0.
- NaN/Inf are sanitized so they can never fabricate a score: a non-finite
  turnover is treated as 0; a non-finite window return is treated as ABSENT for
  that window (the candidate simply is not ranked in it).

``universe_score = (vw*volume_score + mw*momentum_score) / (vw + mw)`` with the
weights normalized; ``momentum_score`` blends per-window percentiles by
``window_weights``, renormalized over the windows each candidate actually has.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

DEFAULT_WINDOW_WEIGHTS: dict[str, float] = {"24h": 0.5, "7d": 0.35, "30d": 0.15}
_DEFAULT_VOLUME_WEIGHT = 0.4
_DEFAULT_MOMENTUM_WEIGHT = 0.6


@dataclass(frozen=True)
class UniverseCandidate:
    """One symbol's raw inputs. ``window_returns_pct`` maps a window label
    (e.g. ``"24h"``, ``"7d"``, ``"30d"``) to that window's % price return."""

    symbol: str
    turnover_24h: float
    window_returns_pct: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RankedSymbol:
    symbol: str
    universe_score: float
    volume_score: float
    momentum_score: float
    rank: int
    components: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class _Scored:
    symbol: str
    universe_score: float
    volume_score: float
    momentum_score: float
    turnover: float
    components: dict[str, float]


def _finite(value: float, default: float = 0.0) -> float:
    """Coerce to a finite float; non-float / NaN / Inf → ``default``."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _percentile_ranks(values: Sequence[float]) -> list[float]:
    """Map each value to the fraction of the field it strictly beats, in [0,1].

    ``n == 1`` → 0.5 (no field to rank against); ties share the same percentile.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    return [sum(1 for other in values if other < v) / (n - 1) for v in values]


def rank_universe(
    candidates: Sequence[UniverseCandidate],
    *,
    top_n: int,
    volume_weight: float = _DEFAULT_VOLUME_WEIGHT,
    momentum_weight: float = _DEFAULT_MOMENTUM_WEIGHT,
    window_weights: Mapping[str, float] | None = None,
) -> list[RankedSymbol]:
    """Rank ``candidates`` by a blended volume+momentum percentile score.

    Returns the top ``top_n`` as ``RankedSymbol`` (rank 1 = best). Deterministic:
    ties break by turnover (desc) then symbol (asc).
    """
    if top_n <= 0:
        raise ValueError("top_n must be >= 1")
    vw = _finite(volume_weight, 0.0)
    mw = _finite(momentum_weight, 0.0)
    if vw < 0 or mw < 0 or (vw + mw) <= 0:
        raise ValueError("volume_weight and momentum_weight must be >= 0 with a positive sum")
    if not candidates:
        return []

    weights = dict(window_weights) if window_weights is not None else dict(DEFAULT_WINDOW_WEIGHTS)

    # Volume percentile across all candidates (turnover sanitized, negatives → 0).
    turnovers = [max(0.0, _finite(c.turnover_24h, 0.0)) for c in candidates]
    volume_scores = _percentile_ranks(turnovers)

    # Per-window percentile, computed only over candidates that actually have a
    # finite return for that window (NaN/Inf returns are treated as absent).
    all_windows = sorted({w for c in candidates for w in c.window_returns_pct})
    window_pct: dict[str, dict[int, float]] = {}
    for w in all_windows:
        present_idx = [
            i
            for i, c in enumerate(candidates)
            if w in c.window_returns_pct
            and math.isfinite(_finite(c.window_returns_pct[w], math.nan))
        ]
        present_vals = [_finite(candidates[i].window_returns_pct[w], 0.0) for i in present_idx]
        window_pct[w] = dict(zip(present_idx, _percentile_ranks(present_vals), strict=True))

    scored: list[_Scored] = []
    for i, c in enumerate(candidates):
        vol = volume_scores[i]
        num = 0.0
        den = 0.0
        components: dict[str, float] = {}
        for w in all_windows:
            if i in window_pct[w]:
                wt = max(0.0, _finite(weights.get(w, 0.0), 0.0))
                pct = window_pct[w][i]
                components[f"ret_{w}"] = pct
                num += wt * pct
                den += wt
        mom = (num / den) if den > 0 else 0.0
        uni = (vw * vol + mw * mom) / (vw + mw)
        components["volume_score"] = vol
        components["momentum_score"] = mom
        scored.append(
            _Scored(
                symbol=c.symbol,
                universe_score=uni,
                volume_score=vol,
                momentum_score=mom,
                turnover=turnovers[i],
                components=components,
            )
        )

    scored.sort(key=lambda s: (-s.universe_score, -s.turnover, s.symbol))
    return [
        RankedSymbol(
            symbol=s.symbol,
            universe_score=s.universe_score,
            volume_score=s.volume_score,
            momentum_score=s.momentum_score,
            rank=rank,
            components=s.components,
        )
        for rank, s in enumerate(scored[:top_n], start=1)
    ]
