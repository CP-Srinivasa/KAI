"""Domain-agnostic threshold optimization on score → realized-P&L observations.

Use cases
---------

Any gating-threshold that compares a continuous score against a cutoff:

  • `signal.thresholds.min_bayes_confidence`  — Bayes confidence gate
  • `signal.thresholds.min_signal_confidence` — LLM-derived analysis gate
  • `signal.thresholds.min_confluence`        — integer confluence count
  • risk-side thresholds, etc.

Inputs are intentionally minimal: each historical decision contributes one
``ThresholdObservation(score, realized_pnl_usd)``.  The caller supplies the
grid (granularity = problem-specific).

Method
------

For each candidate threshold T:

  - n_passing  = #{ obs : obs.score >= T }
  - pnl_total  = sum(obs.pnl) for passing obs

Approve iff:

  - best_pnl − baseline_pnl >= min_pnl_improvement_usd  (positive lift)
  - best_threshold has at least `min_trades_for_threshold` passing obs
    (no over-fitting to a single lucky trade)

Selection-bias caveat (same as ``app/learning/counterfactual.py``):

  We only see realized P&L for trades that the *current* gate let through.
  Lowering the threshold below today's gate gives partial information at
  best — observations that today's gate already rejected don't appear in
  the data.  The optimizer therefore only considers thresholds **>=
  baseline_threshold** by default, refusing to extrapolate downward.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_GRID: Final[tuple[float, ...]] = tuple(
    round(0.50 + 0.05 * i, 2)
    for i in range(10)  # 0.50..0.95
)
DEFAULT_MIN_TRADES_FOR_THRESHOLD: Final[int] = 5
DEFAULT_MIN_PNL_IMPROVEMENT_USD: Final[float] = 0.0
DEFAULT_MIN_OBSERVATIONS: Final[int] = 30

DecisionLiteral = Literal["approve", "reject", "neutral", "insufficient_data"]


# ─── Inputs ───────────────────────────────────────────────────────────────────


class ThresholdObservation(BaseModel):
    """One historical decision: which score it carried, what P&L it realized."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str
    score: float
    realized_pnl_usd: float


class ThresholdConfig(BaseModel):
    """Optimization knobs.  All defaults are explicit + audit-friendly."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    threshold_grid: tuple[float, ...] = DEFAULT_GRID
    min_trades_for_threshold: int = Field(default=DEFAULT_MIN_TRADES_FOR_THRESHOLD, ge=1)
    min_pnl_improvement_usd: float = Field(default=DEFAULT_MIN_PNL_IMPROVEMENT_USD, ge=0.0)
    min_observations: int = Field(default=DEFAULT_MIN_OBSERVATIONS, ge=1)
    only_consider_at_or_above_baseline: bool = True


# ─── Outputs ──────────────────────────────────────────────────────────────────


class ThresholdGridPoint(BaseModel):
    """One row in the grid sweep."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    threshold: float
    n_passing: int
    pnl_total_usd: float
    pnl_mean_per_trade_usd: float


class ThresholdOptimizationReport(BaseModel):
    """Aggregate optimization report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_observations: int
    baseline_threshold: float
    baseline_n_passing: int
    baseline_pnl_usd: float
    grid: tuple[ThresholdGridPoint, ...]
    best_threshold: float | None
    best_pnl_usd: float | None
    pnl_improvement_usd: float
    decision: DecisionLiteral
    decision_reasons: tuple[str, ...]
    config: ThresholdConfig


# ─── Optimizer ────────────────────────────────────────────────────────────────


def _evaluate(observations: Sequence[ThresholdObservation], threshold: float) -> ThresholdGridPoint:
    passing = [o for o in observations if o.score >= threshold]
    n = len(passing)
    pnl = sum(o.realized_pnl_usd for o in passing)
    mean = pnl / n if n > 0 else 0.0
    return ThresholdGridPoint(
        threshold=round(threshold, 6),
        n_passing=n,
        pnl_total_usd=round(pnl, 2),
        pnl_mean_per_trade_usd=round(mean, 4),
    )


def optimize_threshold(
    *,
    observations: Sequence[ThresholdObservation],
    baseline_threshold: float,
    config: ThresholdConfig | None = None,
) -> ThresholdOptimizationReport:
    """Grid-search the threshold that maximizes realized P&L.

    Returns a :class:`ThresholdOptimizationReport` with a hard decision; never
    raises on degenerate input.
    """
    cfg = config or ThresholdConfig()
    n = len(observations)

    baseline_point = _evaluate(observations, baseline_threshold)

    if n < cfg.min_observations:
        return ThresholdOptimizationReport(
            n_observations=n,
            baseline_threshold=baseline_threshold,
            baseline_n_passing=baseline_point.n_passing,
            baseline_pnl_usd=baseline_point.pnl_total_usd,
            grid=(),
            best_threshold=None,
            best_pnl_usd=None,
            pnl_improvement_usd=0.0,
            decision="insufficient_data",
            decision_reasons=(f"have {n} observations, need >= {cfg.min_observations}",),
            config=cfg,
        )

    candidate_thresholds = tuple(cfg.threshold_grid)
    if cfg.only_consider_at_or_above_baseline:
        candidate_thresholds = tuple(t for t in candidate_thresholds if t >= baseline_threshold)

    grid_points = [_evaluate(observations, t) for t in candidate_thresholds]
    eligible = [gp for gp in grid_points if gp.n_passing >= cfg.min_trades_for_threshold]

    if not eligible:
        return ThresholdOptimizationReport(
            n_observations=n,
            baseline_threshold=baseline_threshold,
            baseline_n_passing=baseline_point.n_passing,
            baseline_pnl_usd=baseline_point.pnl_total_usd,
            grid=tuple(grid_points),
            best_threshold=None,
            best_pnl_usd=None,
            pnl_improvement_usd=0.0,
            decision="insufficient_data",
            decision_reasons=(
                f"no threshold in grid passes >= {cfg.min_trades_for_threshold} "
                f"trades (need to broaden grid or collect more data)",
            ),
            config=cfg,
        )

    best = max(eligible, key=lambda gp: gp.pnl_total_usd)
    improvement = round(best.pnl_total_usd - baseline_point.pnl_total_usd, 2)

    decision_reasons: list[str] = []
    decision: DecisionLiteral
    if improvement >= cfg.min_pnl_improvement_usd and improvement > 0:
        decision = "approve"
        decision_reasons.append(
            f"best threshold {best.threshold:.4f} would have produced "
            f"${best.pnl_total_usd:+,.2f} on {best.n_passing} trades vs. "
            f"baseline ${baseline_point.pnl_total_usd:+,.2f} on "
            f"{baseline_point.n_passing} trades (Δ ${improvement:+,.2f})"
        )
    elif improvement <= -cfg.min_pnl_improvement_usd:
        decision = "reject"
        decision_reasons.append(
            f"best in-grid threshold ({best.threshold:.4f}) underperforms "
            f"baseline by ${-improvement:,.2f} — keep current threshold"
        )
    else:
        decision = "neutral"
        decision_reasons.append(
            f"P&L delta ${improvement:+,.2f} within ±"
            f"${cfg.min_pnl_improvement_usd:.2f} band — no actionable edge"
        )

    return ThresholdOptimizationReport(
        n_observations=n,
        baseline_threshold=baseline_threshold,
        baseline_n_passing=baseline_point.n_passing,
        baseline_pnl_usd=baseline_point.pnl_total_usd,
        grid=tuple(grid_points),
        best_threshold=best.threshold,
        best_pnl_usd=best.pnl_total_usd,
        pnl_improvement_usd=improvement,
        decision=decision,
        decision_reasons=tuple(decision_reasons),
        config=cfg,
    )


__all__ = [
    "DEFAULT_GRID",
    "DEFAULT_MIN_OBSERVATIONS",
    "DEFAULT_MIN_PNL_IMPROVEMENT_USD",
    "DEFAULT_MIN_TRADES_FOR_THRESHOLD",
    "ThresholdConfig",
    "ThresholdGridPoint",
    "ThresholdObservation",
    "ThresholdOptimizationReport",
    "optimize_threshold",
]
