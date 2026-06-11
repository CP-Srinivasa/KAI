"""Generator edge measurement (NEO /goal 2026-06-05, Aufgabe 1).

Purpose
-------
Prove or disprove — *defensibly* — whether a given signal generator / agent /
signal-type has real, **cost-adjusted, tradeable** edge. This is the measuring
instrument, not a verdict machine: when the evidence is thin it returns
``INSUFFICIENT``, never a fabricated GO/NO-GO.

This module is **read-only on the trading path**. It composes existing,
tested primitives instead of duplicating their math:

  - ``app.observability.edge_report``  → per-trade cost decomposition
    (``compute_trade_edge``), cohort aggregation (``aggregate_cohort`` →
    win-rate, avg win/loss bps, net_bps, bootstrap P(mu_net>0)), churn.
  - ``app.learning.calibration``       → Brier score + expected calibration
    error (ECE) from (predicted_probability, actual_outcome) pairs.
  - ``app.execution.cost_model``        → the SAME CostModel the engine charges.

Only genuinely new math lives here: per-cohort Sharpe / Sortino / max-drawdown
on the realised net-return series, cohort tail-CVaR, the Information Coefficient
(IC) per horizon with its decay, an overtrading score, and the förderfähig
Go/No-Go gate that combines them.

Honesty contracts (kai-master-coding-regeln §safe, KAI-Directive §9)
--------------------------------------------------------------------
- ``INSUFFICIENT`` ≠ ``NO_GO``. Too few resolved trades means *we cannot judge*,
  which is a different state from *we judged and it failed*. They are never
  conflated.
- Every metric that needs data we do not have is honestly ``None`` with a
  reason, never a placeholder number.
- IC needs forward returns aligned to the signal score; absent that alignment it
  is ``None`` (``no_aligned_forward_samples``), consistent with how
  ``edge_report`` already reports forward coverage.
- "cvar_bps" is the cohort's OWN conditional-loss tail. Portfolio-level CVaR
  *contribution* requires the cross-position covariance path
  (``app.risk.portfolio_risk.PortfolioRiskEngine``) and is out of scope here;
  the field is named to avoid overclaiming.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.execution.cost_model import CostModel
from app.learning.calibration import OutcomePair, compute_calibration
from app.observability.edge_report import (
    ClosedTrade,
    TradeEdge,
    aggregate_cohort,
    compute_churn,
    compute_trade_edge,
)

logger = logging.getLogger(__name__)

# Horizons we report IC for. Kept as labels so the report shape is stable even
# when a horizon has no aligned samples (it then reports IC=None).
IC_HORIZONS: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "24h")

VERDICT_GO = "GO"
VERDICT_NO_GO = "NO_GO"
VERDICT_INSUFFICIENT = "INSUFFICIENT"


# ─── Go/No-Go gate configuration ──────────────────────────────────────────────


@dataclass(frozen=True)
class EdgeGateConfig:
    """Thresholds for the förderfähig (eligible-for-promotion) verdict.

    Deliberately conservative defaults. A generator is only ``GO`` when it
    clears EVERY gate; any single failure is ``NO_GO``; too little data is
    ``INSUFFICIENT`` (judged separately, before the gates run).
    """

    min_resolved: int = 30
    """Below this many resolved trades the verdict is INSUFFICIENT, not NO_GO."""

    min_ev_after_costs_bps: float = 0.0
    """EV after costs must be strictly above this (bps)."""

    min_p_mu_net_positive: float = 0.60
    """Bootstrap probability that the mean net edge is > 0."""

    min_ic_stable: float = 0.0
    """Each *available* IC horizon must be >= this to count as 'stably positive'."""

    min_ic_horizons_positive: int = 2
    """At least this many horizons must be available AND positive."""

    max_ece: float = 0.10
    """Calibration: expected calibration error ceiling (vs a sane baseline)."""

    max_drawdown_bps: float = 2000.0
    """Toxic-drawdown ceiling on the realised net-return equity curve (bps)."""

    min_distinct_regimes: int = 2
    """Edge must not come from a single lucky regime (when regime data exists)."""


# ─── per-cohort profile ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class GeneratorEdgeProfile:
    """Full edge profile for one cohort (generator / signal_type / regime / horizon).

    Optional fields are ``None`` when the underlying data is insufficient — that
    is a first-class, honest state, not an error.
    """

    cohort_key: str
    cohort_type: str  # "generator" | "signal_type" | "regime" | "generator|regime"

    trade_count: int
    resolved_count: int
    win_rate: float | None
    payoff_ratio: float | None
    expected_value_before_costs_bps: float | None
    expected_value_after_costs_bps: float | None
    fees_impact_bps: float | None
    slippage_impact_bps: float | None
    latency_impact_bps: float | None
    p_mu_net_positive: float | None

    ic_by_horizon: dict[str, float | None]
    signal_decay: dict[str, float | None]

    brier_score: float | None
    calibration_error: float | None  # ECE

    sharpe: float | None
    sortino: float | None
    max_drawdown_bps: float | None
    cvar_bps: float | None  # cohort tail-CVaR (NOT portfolio contribution)

    overtrading_score: float | None
    distinct_regimes: int

    verdict: str  # GO | NO_GO | INSUFFICIENT
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        def _r(x: float | None, n: int = 4) -> float | None:
            return None if x is None else round(x, n)

        return {
            "cohort_key": self.cohort_key,
            "cohort_type": self.cohort_type,
            "trade_count": self.trade_count,
            "resolved_count": self.resolved_count,
            "win_rate": _r(self.win_rate),
            "payoff_ratio": _r(self.payoff_ratio),
            "expected_value_before_costs_bps": _r(self.expected_value_before_costs_bps),
            "expected_value_after_costs_bps": _r(self.expected_value_after_costs_bps),
            "fees_impact_bps": _r(self.fees_impact_bps),
            "slippage_impact_bps": _r(self.slippage_impact_bps),
            "latency_impact_bps": _r(self.latency_impact_bps),
            "p_mu_net_positive": _r(self.p_mu_net_positive),
            "ic_by_horizon": {k: _r(v, 6) for k, v in self.ic_by_horizon.items()},
            "signal_decay": {k: _r(v, 6) for k, v in self.signal_decay.items()},
            "brier_score": _r(self.brier_score, 6),
            "calibration_error": _r(self.calibration_error, 6),
            "sharpe": _r(self.sharpe),
            "sortino": _r(self.sortino),
            "max_drawdown_bps": _r(self.max_drawdown_bps),
            "cvar_bps": _r(self.cvar_bps),
            "overtrading_score": _r(self.overtrading_score),
            "distinct_regimes": self.distinct_regimes,
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class GeneratorEdgeReport:
    """Top-level report: per-cohort profiles + an honest data-sufficiency banner."""

    profiles: tuple[GeneratorEdgeProfile, ...]
    total_resolved: int
    cutoff_utc: str | None
    gate_config: EdgeGateConfig
    data_sufficiency: str  # "sufficient" | "insufficient"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_resolved": self.total_resolved,
            "cutoff_utc": self.cutoff_utc,
            "data_sufficiency": self.data_sufficiency,
            "gate_config": {
                "min_resolved": self.gate_config.min_resolved,
                "min_ev_after_costs_bps": self.gate_config.min_ev_after_costs_bps,
                "min_p_mu_net_positive": self.gate_config.min_p_mu_net_positive,
                "min_ic_horizons_positive": self.gate_config.min_ic_horizons_positive,
                "max_ece": self.gate_config.max_ece,
                "max_drawdown_bps": self.gate_config.max_drawdown_bps,
                "min_distinct_regimes": self.gate_config.min_distinct_regimes,
            },
            "notes": list(self.notes),
            "profiles": [p.to_dict() for p in self.profiles],
        }


# ─── self-contained math (kept small; reuses edge_report/calibration elsewhere) ─


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    """Sample standard deviation (ddof=1). 0.0 for n < 2."""
    n = len(xs)
    if n < 2:
        return 0.0
    mu = _mean(xs)
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation. None when undefined (n<2 or zero variance)."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sxx * syy)


def sharpe_ratio(returns: Sequence[float]) -> float | None:
    """Per-trade Sharpe on a net-return series. None for n<2 or zero dispersion."""
    if len(returns) < 2:
        return None
    sd = _std(returns)
    if sd <= 0.0:
        return None
    return _mean(returns) / sd


def sortino_ratio(returns: Sequence[float], *, mar: float = 0.0) -> float | None:
    """Per-trade Sortino: mean excess over downside deviation. None if undefined."""
    if len(returns) < 2:
        return None
    downside = [min(0.0, r - mar) for r in returns]
    dd = math.sqrt(sum(d**2 for d in downside) / len(returns))
    if dd <= 0.0:
        return None  # no downside observed → ratio undefined (not infinite)
    return (_mean(returns) - mar) / dd


def max_drawdown_bps(returns: Sequence[float]) -> float | None:
    """Max drawdown (in bps) of the cumulative net-return equity curve.

    ``returns`` are per-trade net bps in chronological order. Returns a
    non-negative magnitude. None for an empty series.
    """
    if not returns:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in returns:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def cohort_cvar_bps(returns: Sequence[float], *, alpha: float = 0.95) -> float | None:
    """Historical conditional loss (CVaR) of the cohort's own net-return tail.

    Returns a non-negative loss magnitude in bps (expected loss in the worst
    ``1-alpha`` tail). None below a minimal sample. This is the cohort's own
    tail risk, NOT its contribution to a portfolio's covariance-based CVaR.
    """
    n = len(returns)
    if n < 5:
        return None
    losses = sorted((-r for r in returns), reverse=True)  # largest loss first
    k = max(1, int(round(n * (1.0 - alpha))))
    tail = losses[:k]
    return max(0.0, _mean(tail))


# ─── IC + decay ───────────────────────────────────────────────────────────────


def compute_ic_by_horizon(
    aligned: Mapping[str, Sequence[tuple[float, float]]] | None,
    *,
    min_sample: int = 20,
) -> dict[str, float | None]:
    """Information Coefficient per horizon.

    ``aligned`` maps a horizon label (e.g. ``"1h"``) to a list of
    ``(signal_score, forward_return)`` pairs that were actually observed for
    this cohort at that horizon. Missing/short horizons report ``None`` — the
    instrument never invents an IC. Result keys are always the full
    ``IC_HORIZONS`` set so the report shape is stable.
    """
    out: dict[str, float | None] = dict.fromkeys(IC_HORIZONS, None)
    if not aligned:
        return out
    for horizon in IC_HORIZONS:
        pairs = aligned.get(horizon)
        if not pairs or len(pairs) < min_sample:
            continue
        scores = [s for s, _ in pairs]
        fwd = [f for _, f in pairs]
        out[horizon] = _pearson(scores, fwd)
    return out


def compute_signal_decay(ic_by_horizon: Mapping[str, float | None]) -> dict[str, float | None]:
    """Signal decay = IC_h / IC_initial, where IC_initial is the first available,
    positive, shortest-horizon IC. None where IC is missing or the baseline is
    not a positive number.
    """
    out: dict[str, float | None] = dict.fromkeys(ic_by_horizon.keys(), None)
    baseline: float | None = None
    for horizon in IC_HORIZONS:
        ic = ic_by_horizon.get(horizon)
        if ic is not None and ic > 0.0:
            baseline = ic
            break
    if baseline is None or baseline <= 0.0:
        return out
    for horizon, ic in ic_by_horizon.items():
        if ic is not None:
            out[horizon] = ic / baseline
    return out


# ─── förderfähig (eligible) verdict ───────────────────────────────────────────


def evaluate_verdict(
    *,
    resolved_count: int,
    ev_after_costs_bps: float | None,
    p_mu_net_positive: float | None,
    ic_by_horizon: Mapping[str, float | None],
    calibration_error: float | None,
    max_dd_bps: float | None,
    distinct_regimes: int,
    config: EdgeGateConfig,
) -> tuple[str, tuple[str, ...]]:
    """Return (verdict, reason_codes).

    INSUFFICIENT is decided FIRST and short-circuits: with too few resolved
    trades we cannot judge, so we do not pretend to. Only with enough data do
    the GO/NO-GO gates run; a single failed gate is NO_GO.
    """
    if resolved_count < config.min_resolved:
        return VERDICT_INSUFFICIENT, (f"resolved_count={resolved_count}<{config.min_resolved}",)

    reasons: list[str] = []

    if ev_after_costs_bps is None:
        reasons.append("ev_after_costs=None")
    elif ev_after_costs_bps <= config.min_ev_after_costs_bps:
        reasons.append(
            f"ev_after_costs={ev_after_costs_bps:.2f}bps<={config.min_ev_after_costs_bps}"
        )

    if p_mu_net_positive is None:
        reasons.append("p_mu_net_positive=None")
    elif p_mu_net_positive < config.min_p_mu_net_positive:
        reasons.append(f"p_mu_net_positive={p_mu_net_positive:.2f}<{config.min_p_mu_net_positive}")

    positive_horizons = [
        h for h, ic in ic_by_horizon.items() if ic is not None and ic >= config.min_ic_stable
    ]
    available_horizons = [h for h, ic in ic_by_horizon.items() if ic is not None]
    if len(available_horizons) == 0:
        reasons.append("ic_unavailable")
    elif len(positive_horizons) < config.min_ic_horizons_positive:
        reasons.append(
            f"ic_positive_horizons={len(positive_horizons)}<{config.min_ic_horizons_positive}"
        )

    if calibration_error is None:
        reasons.append("calibration_unavailable")
    elif calibration_error > config.max_ece:
        reasons.append(f"ece={calibration_error:.3f}>{config.max_ece}")

    if max_dd_bps is None:
        reasons.append("drawdown_unavailable")
    elif max_dd_bps > config.max_drawdown_bps:
        reasons.append(f"max_dd={max_dd_bps:.0f}bps>{config.max_drawdown_bps:.0f}")

    if distinct_regimes < config.min_distinct_regimes:
        reasons.append(f"distinct_regimes={distinct_regimes}<{config.min_distinct_regimes}")

    if reasons:
        return VERDICT_NO_GO, tuple(reasons)
    return VERDICT_GO, ("all_gates_passed",)


# ─── builder ──────────────────────────────────────────────────────────────────


def build_cohort_profile(
    cohort_key: str,
    cohort_type: str,
    trades: Sequence[ClosedTrade],
    *,
    cost_model: CostModel | None = None,
    venue: str = "paper",
    signal_count: int | None = None,
    ic_aligned: Mapping[str, Sequence[tuple[float, float]]] | None = None,
    outcome_pairs: Sequence[OutcomePair] | None = None,
    latency_impact_bps: float | None = None,
    config: EdgeGateConfig | None = None,
) -> GeneratorEdgeProfile:
    """Assemble one cohort's full edge profile from existing primitives.

    ``trades`` are the resolved (closed) round-trips for this cohort, in
    chronological order. ``signal_count`` is the number of signals the generator
    *emitted* (>= resolved); when unknown it defaults to ``resolved_count`` and a
    reason is added. ``ic_aligned`` / ``outcome_pairs`` are honest gaps when
    absent.
    """
    cfg = config or EdgeGateConfig()
    cm = cost_model or CostModel()

    edges: list[TradeEdge] = [compute_trade_edge(t, cm, venue=venue) for t in trades]
    resolved = len(edges)
    trade_count = signal_count if signal_count is not None else resolved

    if resolved == 0:
        ic = compute_ic_by_horizon(ic_aligned)
        # #170 Part B: a SHADOW-measured cohort has no closed trades by design,
        # but its side-channel evidence (IC alignment + calibration pairs from
        # the resolver) is real measurement — report it instead of muting it.
        zero_brier: float | None = None
        zero_ece: float | None = None
        if outcome_pairs:
            zero_calib = compute_calibration(list(outcome_pairs))
            zero_brier = zero_calib.brier_score
            zero_ece = zero_calib.expected_calibration_error
        verdict, reasons = evaluate_verdict(
            resolved_count=0,
            ev_after_costs_bps=None,
            p_mu_net_positive=None,
            ic_by_horizon=ic,
            calibration_error=zero_ece,
            max_dd_bps=None,
            distinct_regimes=0,
            config=cfg,
        )
        return GeneratorEdgeProfile(
            cohort_key=cohort_key,
            cohort_type=cohort_type,
            trade_count=trade_count,
            resolved_count=0,
            win_rate=None,
            payoff_ratio=None,
            expected_value_before_costs_bps=None,
            expected_value_after_costs_bps=None,
            fees_impact_bps=None,
            slippage_impact_bps=None,
            latency_impact_bps=latency_impact_bps,
            p_mu_net_positive=None,
            ic_by_horizon=ic,
            signal_decay=compute_signal_decay(ic),
            brier_score=zero_brier,
            calibration_error=zero_ece,
            sharpe=None,
            sortino=None,
            max_drawdown_bps=None,
            cvar_bps=None,
            overtrading_score=None,
            distinct_regimes=0,
            verdict=verdict,
            reason_codes=reasons,
        )

    cohort = aggregate_cohort(cohort_key, cohort_type, edges)

    # EV decomposition — net comes from the SAME CostModel the engine charges.
    ev_before = cohort.gross_bps_mean
    ev_after = cohort.net_bps_mean
    fees_impact = cohort.fee_bps_mean
    slippage_impact = cohort.slippage_bps_mean

    # payoff ratio = avg win / |avg loss| (bps). None when there are no losses.
    payoff: float | None = None
    if cohort.avg_loss_bps < 0.0:
        payoff = cohort.avg_win_bps / abs(cohort.avg_loss_bps)
    elif cohort.avg_win_bps > 0.0:
        payoff = None  # no losses observed → ratio undefined, not infinite

    net_series = [e.net_bps for e in edges]
    sharpe = sharpe_ratio(net_series)
    sortino = sortino_ratio(net_series)
    mdd = max_drawdown_bps(net_series)
    cvar = cohort_cvar_bps(net_series)

    ic = compute_ic_by_horizon(ic_aligned)
    decay = compute_signal_decay(ic)

    calib = compute_calibration(list(outcome_pairs or []))
    brier = calib.brier_score
    ece = calib.expected_calibration_error

    distinct_regimes = len({t.regime for t in trades if t.regime and t.regime != "unknown"})

    # overtrading: mean re-entries/day across symbols in this cohort. Higher =
    # more churn. None when day attribution is unavailable.
    churn = compute_churn(trades)
    overtrading = _mean([c.reentries_per_day for c in churn]) if churn else None

    verdict, reasons = evaluate_verdict(
        resolved_count=resolved,
        ev_after_costs_bps=ev_after,
        p_mu_net_positive=cohort.p_mu_net_positive,
        ic_by_horizon=ic,
        calibration_error=ece,
        max_dd_bps=mdd,
        distinct_regimes=distinct_regimes,
        config=cfg,
    )

    return GeneratorEdgeProfile(
        cohort_key=cohort_key,
        cohort_type=cohort_type,
        trade_count=trade_count,
        resolved_count=resolved,
        win_rate=cohort.winrate,
        payoff_ratio=payoff,
        expected_value_before_costs_bps=ev_before,
        expected_value_after_costs_bps=ev_after,
        fees_impact_bps=fees_impact,
        slippage_impact_bps=slippage_impact,
        latency_impact_bps=latency_impact_bps,
        p_mu_net_positive=cohort.p_mu_net_positive,
        ic_by_horizon=ic,
        signal_decay=decay,
        brier_score=brier,
        calibration_error=ece,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_bps=mdd,
        cvar_bps=cvar,
        overtrading_score=overtrading,
        distinct_regimes=distinct_regimes,
        verdict=verdict,
        reason_codes=reasons,
    )


def build_generator_edge_report(
    trades: Sequence[ClosedTrade],
    *,
    cohort_type: str = "generator",
    cost_model: CostModel | None = None,
    venue: str = "paper",
    signal_counts: Mapping[str, int] | None = None,
    ic_aligned_by_cohort: Mapping[str, Mapping[str, Sequence[tuple[float, float]]]] | None = None,
    outcome_pairs_by_cohort: Mapping[str, Sequence[OutcomePair]] | None = None,
    latency_impact_by_cohort: Mapping[str, float] | None = None,
    cutoff_utc: str | None = None,
    config: EdgeGateConfig | None = None,
) -> GeneratorEdgeReport:
    """Group resolved trades by generator (``signal_source``) and profile each.

    ``cohort_type`` selects the grouping key on ``ClosedTrade``:
      - ``"generator"``   → ``signal_source``
      - ``"regime"``      → ``regime``
      - ``"symbol"``      → ``symbol``

    All side-channel inputs (signal counts, IC alignment, outcome pairs, latency)
    are optional maps keyed by cohort; absent entries degrade honestly.
    """
    cfg = config or EdgeGateConfig()

    def _key(t: ClosedTrade) -> str:
        if cohort_type == "regime":
            return t.regime or "unknown"
        if cohort_type == "symbol":
            return t.symbol
        return t.signal_source or "unknown"

    grouped: dict[str, list[ClosedTrade]] = {}
    for t in trades:
        grouped.setdefault(_key(t), []).append(t)

    # #170 Part B: cohorts that exist ONLY in the side-channel evidence (the
    # shadow-measured generator stream produces no closed trades by design)
    # still get a profile — IC/Brier are real measurement; the trade-based
    # metrics degrade honestly to None/INSUFFICIENT.
    side_only = (set(ic_aligned_by_cohort or {}) | set(outcome_pairs_by_cohort or {})) - set(
        grouped
    )
    for key in side_only:
        grouped[key] = []

    profiles: list[GeneratorEdgeProfile] = []
    for key in sorted(grouped):
        cohort_trades = grouped[key]
        profiles.append(
            build_cohort_profile(
                key,
                cohort_type,
                cohort_trades,
                cost_model=cost_model,
                venue=venue,
                signal_count=(signal_counts or {}).get(key),
                ic_aligned=(ic_aligned_by_cohort or {}).get(key),
                outcome_pairs=(outcome_pairs_by_cohort or {}).get(key),
                latency_impact_bps=(latency_impact_by_cohort or {}).get(key),
                config=cfg,
            )
        )

    total_resolved = len(trades)
    sufficiency = "sufficient" if total_resolved >= cfg.min_resolved else "insufficient"
    notes: list[str] = []
    if sufficiency == "insufficient":
        notes.append(
            f"total_resolved={total_resolved}<{cfg.min_resolved}: report is the measuring "
            "instrument; verdicts are INSUFFICIENT until the real-analysis feeder lands "
            "(NEO-P-002-r3). This is an honest data state, not a defect."
        )

    return GeneratorEdgeReport(
        profiles=tuple(profiles),
        total_resolved=total_resolved,
        cutoff_utc=cutoff_utc,
        gate_config=cfg,
        data_sufficiency=sufficiency,
        notes=tuple(notes),
    )
