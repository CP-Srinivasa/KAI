"""Risk-adjusted Agent Scoreboard — rank agents by edge QUALITY, not raw PnL.

Watchdog goal (2026-06-05): fuse the learning, calibration, walk-forward and
trade data KAI already records into one risk-adjusted ranking, so that:

  * an agent with high PnL but a TOXIC drawdown does not sit at the top, and
  * an agent with good calibration and a smaller, STABLE edge can rank higher.

The score uses the operator-specified weights exactly:

    agent_score =
        0.20 * EV_after_costs
      + 0.15 * Sharpe
      + 0.15 * Sortino
      + 0.15 * calibration_quality
      + 0.10 * IC_stability
      + 0.10 * drawdown_quality
      + 0.10 * regime_robustness
      - 0.05 * overtrading_penalty

Because the raw metrics live on incompatible scales (EV in bps, Sharpe
unbounded, Brier in [0,1], drawdown a negative fraction, …), each term is a
documented monotone normaliser into [0, 1] where 1 == best. The raw metrics
AND their normalised sub-scores are both reported, so the ranking is fully
auditable — no hidden constants.

Displayed-but-not-weighted metrics (operator asked to *show* these; the formula
above intentionally does not weight all of them): PnL, Max Drawdown, CVaR
contribution, Hit Rate, Payoff Ratio, Brier Score, Calibration Error,
IC by horizon, Signal Decay, Overtrading Penalty, regime performance,
source_quality_dependency. They are surfaced as transparency / risk flags.

KAI-no-prediction rule: every metric here is a property of REALISED outcomes
(realised EV, realised drawdown, realised calibration error). Nothing forecasts
future performance; the ranking says "who has behaved well, risk-adjusted".

This is a pure scoring core. It does not authorise any agent to trade — ranking
is advisory monitoring output consumed by the operator and the promotion gate,
never a live-trading trigger.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Weights — operator-specified, exact.
# ---------------------------------------------------------------------------
WEIGHTS: dict[str, float] = {
    "ev_after_costs": 0.20,
    "sharpe": 0.15,
    "sortino": 0.15,
    "calibration_quality": 0.15,
    "ic_stability": 0.10,
    "drawdown_quality": 0.10,
    "regime_robustness": 0.10,
    "overtrading_penalty": -0.05,
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ---------------------------------------------------------------------------
# Normalisers — each maps a raw metric to a [0, 1] quality (1 == best), with
# explicit, documented anchor points. Linear-clamped for transparency.
# ---------------------------------------------------------------------------
def normalize_ev(ev_bps_per_notional: float | None) -> float:
    """EV after costs (net bps per notional) → quality.

    Anchors: -50 bps → 0.0, 0 bps → 0.5, +50 bps → 1.0 (linear, clamped).
    Zero net edge is neutral (0.5); negative edge sits below neutral.
    """
    if ev_bps_per_notional is None:
        return 0.5
    return _clamp01(0.5 + ev_bps_per_notional / 100.0)


def normalize_sharpe(sharpe: float | None) -> float:
    """Sharpe ratio → quality. Anchors: -2 → 0, 0 → 0.5, +2 → 1.0."""
    if sharpe is None:
        return 0.5
    return _clamp01(0.5 + sharpe / 4.0)


def normalize_sortino(sortino: float | None) -> float:
    """Sortino ratio → quality. Same anchors as Sharpe (documented choice).

    Sortino only penalises downside deviation, so it is typically >= Sharpe;
    using the same anchors keeps the two terms comparable and conservative.
    """
    if sortino is None:
        return 0.5
    return _clamp01(0.5 + sortino / 4.0)


def normalize_calibration(
    brier: float | None,
    calibration_error: float | None,
) -> float:
    """Calibration quality from Brier score and/or expected calibration error.

    - Brier (binary outcomes): 0 == perfect, 0.25 == a coin flip (p=0.5).
      quality_brier = (0.25 - brier) / 0.25, clamped → brier 0 ⇒ 1, >=0.25 ⇒ 0.
    - ECE: 0 == perfectly calibrated. quality_ece = 1 - ECE/0.25, clamped.

    When both are present the result is their mean. When neither is present we
    return 0.5 (unknown) rather than rewarding the agent.
    """
    parts: list[float] = []
    if brier is not None:
        parts.append(_clamp01((0.25 - brier) / 0.25))
    if calibration_error is not None:
        parts.append(_clamp01(1.0 - calibration_error / 0.25))
    if not parts:
        return 0.5
    return sum(parts) / len(parts)


def normalize_ic_stability(ic_by_horizon: dict[str, float] | None) -> float:
    """IC stability from information coefficients across horizons.

    Rewards a positive mean IC that is CONSISTENT across horizons. An agent
    whose IC swings sign between horizons is penalised even if its mean is fine.

        mean_q = 0.5 + mean(IC)/2            (IC in [-1,1] ⇒ mean_q in [0,1])
        consistency = 1 - min(1, stdev(IC))  (tight spread ⇒ ~1)
        ic_stability = clamp(mean_q * consistency)

    Empty / missing ⇒ 0.5 (unknown).
    """
    if not ic_by_horizon:
        return 0.5
    ics = [float(v) for v in ic_by_horizon.values()]
    mean_ic = sum(ics) / len(ics)
    mean_q = _clamp01(0.5 + mean_ic / 2.0)
    if len(ics) < 2:
        return mean_q
    var = sum((x - mean_ic) ** 2 for x in ics) / len(ics)
    std = math.sqrt(var)
    consistency = 1.0 - min(1.0, std)
    return _clamp01(mean_q * consistency)


def normalize_drawdown(max_drawdown: float | None) -> float:
    """Drawdown quality from max drawdown (a negative fraction, e.g. -0.4).

    Anchors: 0 → 1.0 (no drawdown), -0.50 → 0.0 (a 50% drawdown is treated as
    fully toxic). quality = 1 - |dd| / 0.5, clamped. This is the term that keeps
    a high-PnL / toxic-drawdown agent off the top spot.
    """
    if max_drawdown is None:
        return 0.5
    return _clamp01(1.0 - abs(max_drawdown) / 0.5)


def normalize_regime_robustness(
    regime_ev_bps: dict[str, float] | None,
) -> float:
    """Regime robustness from per-regime EV (bps).

    Blends average and worst-case so an agent must be both broadly and
    minimally robust:  0.5 * mean_quality + 0.5 * worst_quality, where each
    regime's EV is passed through :func:`normalize_ev`. Missing ⇒ 0.5.
    """
    if not regime_ev_bps:
        return 0.5
    qualities = [normalize_ev(v) for v in regime_ev_bps.values()]
    mean_q = sum(qualities) / len(qualities)
    worst_q = min(qualities)
    return _clamp01(0.5 * mean_q + 0.5 * worst_q)


@dataclass(frozen=True)
class AgentMetricInputs:
    """Raw, realised metrics for one agent. Optional fields → neutral handling.

    Units:
      ev_after_costs_bps      net bps per notional (cost-adjusted realised EV)
      sharpe / sortino        annualised-or-period ratios (caller-consistent)
      max_drawdown            negative fraction (e.g. -0.35 == 35% drawdown)
      cvar_contribution       fraction of portfolio tail risk (display only)
      hit_rate                [0,1] realised win fraction (display only)
      payoff_ratio            avg win / avg loss (display only)
      brier / calibration_error  realised calibration (lower == better)
      ic_by_horizon           {horizon: IC in [-1,1]}
      signal_decay            [0,1], higher == faster edge decay (display)
      overtrading_penalty     [0,1], higher == more overtrading (weighted)
      regime_ev_bps           {regime: net bps}
      source_quality_dependency [0,1], higher == more reliant on low-rep
                                sources (display / risk flag)
    """

    agent_id: str
    # Weighted-into-score (raw):
    ev_after_costs_bps: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    brier: float | None = None
    calibration_error: float | None = None
    ic_by_horizon: dict[str, float] | None = None
    max_drawdown: float | None = None
    regime_ev_bps: dict[str, float] | None = None
    overtrading_penalty: float | None = None
    # Display-only:
    pnl_usd: float | None = None
    cvar_contribution: float | None = None
    hit_rate: float | None = None
    payoff_ratio: float | None = None
    signal_decay: float | None = None
    source_quality_dependency: float | None = None
    n_trades: int = 0
    n_signals: int = 0


@dataclass(frozen=True)
class AgentScore:
    """Auditable per-agent score: raw metrics + normalised sub-scores + total."""

    agent_id: str
    agent_score: float  # raw weighted sum (can be slightly < 0)
    agent_score_clamped: float  # [0, 1] for display
    subscores: dict[str, float]  # normalised [0,1] terms used in the score
    metrics: dict[str, float | None]  # raw realised metrics (display)
    risk_flags: list[str] = field(default_factory=list)
    n_trades: int = 0
    n_signals: int = 0

    def to_json_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "agent_score": round(self.agent_score, 6),
            "agent_score_clamped": round(self.agent_score_clamped, 6),
            "subscores": {k: round(v, 6) for k, v in self.subscores.items()},
            "metrics": {
                k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.metrics.items()
            },
            "risk_flags": list(self.risk_flags),
            "n_trades": self.n_trades,
            "n_signals": self.n_signals,
        }


# Risk-flag thresholds (display-layer guardrails, not part of the score).
_TOXIC_DRAWDOWN: float = 0.30  # |max_dd| > 30% → toxic-drawdown flag
_HIGH_OVERTRADING: float = 0.60
_NEGATIVE_EV_FLAG: float = 0.0
_HIGH_SOURCE_DEPENDENCY: float = 0.60
_POOR_CALIBRATION_QUALITY: float = 0.35


def score_agent(inp: AgentMetricInputs) -> AgentScore:
    """Compute the risk-adjusted score for one agent. Pure, deterministic."""
    subscores = {
        "ev_after_costs": normalize_ev(inp.ev_after_costs_bps),
        "sharpe": normalize_sharpe(inp.sharpe),
        "sortino": normalize_sortino(inp.sortino),
        "calibration_quality": normalize_calibration(inp.brier, inp.calibration_error),
        "ic_stability": normalize_ic_stability(inp.ic_by_horizon),
        "drawdown_quality": normalize_drawdown(inp.max_drawdown),
        "regime_robustness": normalize_regime_robustness(inp.regime_ev_bps),
        "overtrading_penalty": _clamp01(
            inp.overtrading_penalty if inp.overtrading_penalty is not None else 0.0
        ),
    }
    total = sum(WEIGHTS[k] * subscores[k] for k in WEIGHTS)

    flags: list[str] = []
    if inp.max_drawdown is not None and abs(inp.max_drawdown) > _TOXIC_DRAWDOWN:
        flags.append(
            f"toxic_drawdown: max_drawdown={inp.max_drawdown:.2%} exceeds "
            f"{_TOXIC_DRAWDOWN:.0%} — high PnL alone must not promote this agent"
        )
    if inp.overtrading_penalty is not None and inp.overtrading_penalty > _HIGH_OVERTRADING:
        flags.append(f"overtrading: penalty={inp.overtrading_penalty:.2f}")
    if inp.ev_after_costs_bps is not None and inp.ev_after_costs_bps <= _NEGATIVE_EV_FLAG:
        flags.append(f"no_edge_after_costs: EV={inp.ev_after_costs_bps:.1f} bps/notional <= 0")
    if subscores["calibration_quality"] < _POOR_CALIBRATION_QUALITY and (
        inp.brier is not None or inp.calibration_error is not None
    ):
        flags.append("poor_calibration: confidence not backed by realised hit-rate")
    if (
        inp.source_quality_dependency is not None
        and inp.source_quality_dependency > _HIGH_SOURCE_DEPENDENCY
    ):
        flags.append(
            f"source_quality_dependency={inp.source_quality_dependency:.2f}: "
            "edge leans on low-reputation sources"
        )

    metrics: dict[str, float | None] = {
        "pnl_usd": inp.pnl_usd,
        "ev_after_costs_bps": inp.ev_after_costs_bps,
        "sharpe": inp.sharpe,
        "sortino": inp.sortino,
        "max_drawdown": inp.max_drawdown,
        "cvar_contribution": inp.cvar_contribution,
        "hit_rate": inp.hit_rate,
        "payoff_ratio": inp.payoff_ratio,
        "brier": inp.brier,
        "calibration_error": inp.calibration_error,
        "signal_decay": inp.signal_decay,
        "overtrading_penalty": inp.overtrading_penalty,
        "source_quality_dependency": inp.source_quality_dependency,
        "mean_ic": (
            sum(inp.ic_by_horizon.values()) / len(inp.ic_by_horizon) if inp.ic_by_horizon else None
        ),
    }

    return AgentScore(
        agent_id=inp.agent_id,
        agent_score=total,
        agent_score_clamped=_clamp01(total),
        subscores=subscores,
        metrics=metrics,
        risk_flags=flags,
        n_trades=inp.n_trades,
        n_signals=inp.n_signals,
    )


def build_agent_scoreboard(
    inputs: list[AgentMetricInputs],
    *,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """Rank a batch of agents into a JSON-serialisable scoreboard report."""
    now = now_utc or datetime.now(UTC)
    scores = [score_agent(i) for i in inputs]
    ranked = sorted(scores, key=lambda s: s.agent_score, reverse=True)
    return {
        "report_type": "agent_scoreboard",
        "generated_at": now.isoformat(),
        "weights": dict(WEIGHTS),
        "ranking_basis": "risk_adjusted_agent_score",
        "invariant": "ranking_is_advisory_not_execution_authority",
        "n_agents": len(ranked),
        "ranking": [{"rank": i + 1, **s.to_json_dict()} for i, s in enumerate(ranked)],
    }


__all__ = [
    "WEIGHTS",
    "AgentMetricInputs",
    "AgentScore",
    "build_agent_scoreboard",
    "normalize_calibration",
    "normalize_drawdown",
    "normalize_ev",
    "normalize_ic_stability",
    "normalize_regime_robustness",
    "normalize_sharpe",
    "normalize_sortino",
    "score_agent",
]
