"""Edge Release Policy — periodic, cost-adjusted promotion decision (Sprint D).

What this is (and is NOT)
-------------------------
This is the Goal-2026-06-01 **release-decision engine**, not a per-cycle runtime
gate. The runtime kill-switch already exists: it is ``execution.entry_mode``
(Sprint A). Forward edge is not reliably known at entry time, so there is no
honest per-cycle "is this trade +EV?" gate.

Instead this module runs *periodically* against the realised, cost-adjusted
evidence produced by Sprint C (``CohortEdge`` from ``app.observability.edge_report``)
and EMITS a recommended ``EntryMode``. It never writes ``entry_mode``. It never
auto-promotes to live. Promotion to any live mode is an explicit operator action,
flagged here with ``requires_operator_signoff``.

Why a separate module (Risk, not Observability)
------------------------------------------------
Observability *measures* edge. Risk *decides* what trading cadence that edge may
unlock. Folding the decision into ``edge_report`` would entangle a diagnostic with
a governance action. This engine is pure (no IO, no settings mutation, no trading
path) and consumes the exact same ``CohortEdge`` numbers the report renders.

Single cost source
-------------------
``CohortEdge.net_bps_per_notional_mean`` is already cost-adjusted via the SAME
``CostModel`` the engine charges (Sprint B). This engine adds NO second fee path;
it only thresholds the numbers it is handed.

Policy ladder (Goal mapping)
----------------------------
Given a cohort's ``p_mu_net_positive`` (P) and ``net_bps_per_notional_mean`` (net):

  - P is None OR count < min_n            -> DISABLED   (no defensible posterior)
  - net <= safety_margin_bps              -> max PAPER  (positive P but no real edge)
  - P < 0.50                              -> DISABLED
  - 0.50 <= P < 0.80                      -> PAPER      (probe-grade evidence)
  - 0.80 <= P < 0.95  AND net > margin    -> LIVE_LIMITED (operator sign-off)
  - P >= 0.95  AND net > margin AND OOS   -> LIVE_NORMAL *eligible* (operator sign-off)
  - P >= 0.95  AND net > margin AND !OOS  -> LIVE_LIMITED (cannot reach normal)

Hard invariants (never violated, each unit-tested):
  1. No defensible posterior (P None / n<min_n) => DISABLED. "If no defensible
     posterior exists, live must not be auto-released."
  2. net <= margin caps the recommendation at PAPER regardless of P. A high P on a
     net edge that does not beat costs is not an edge.
  3. Any live_* recommendation sets ``requires_operator_signoff=True``. The engine
     NEVER returns an auto-promote signal — LIVE_NORMAL is returned only as
     "eligible, requires explicit operator promotion".
  4. ``oos_stable=False`` blocks LIVE_NORMAL (downgraded to LIVE_LIMITED).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.enums import EntryMode
from app.observability.edge_report import CohortEdge

logger = logging.getLogger(__name__)

# Posterior thresholds (Goal-defined ladder). Inclusive lower bounds.
P_DISABLED_MAX = 0.50  # P < this -> DISABLED
P_PAPER_MAX = 0.80  # P in [0.50, 0.80) -> PAPER
P_LIVE_LIMITED_MAX = 0.95  # P in [0.80, 0.95) -> LIVE_LIMITED; >= this -> LIVE_NORMAL eligible

# A cohort below this many closed round-trips has no defensible posterior even if
# a P happens to be computed; we refuse to release on it. Matches the spirit of
# edge_report.MIN_SAMPLE_FOR_P but is the RELEASE gate's own, stricter knob.
DEFAULT_MIN_N = 20

# Default extra edge a cohort must clear (net_bps_per_notional_mean) before any
# live recommendation. 0.0 means "must be strictly cost-positive". The operator
# can raise this to demand a buffer above breakeven.
DEFAULT_SAFETY_MARGIN_BPS = 0.0

# Out-of-sample stability: P must hold across at least this many disjoint daily
# cohorts (deterministic, documented). Below it, oos_stable is False and
# LIVE_NORMAL is unreachable.
DEFAULT_OOS_MIN_DISJOINT_DAYS = 2


@dataclass(frozen=True)
class ReleaseDecision:
    """Typed, JSON-serialisable release decision for one evaluated cohort.

    ``recommended_mode`` is a recommendation only. ``current_mode`` is whatever
    the system is configured with (``settings.execution.entry_mode``). The engine
    NEVER changes ``current_mode``; surfacing both lets the operator see drift.
    """

    recommended_mode: EntryMode
    current_mode: EntryMode | None
    p_mu_net_positive: float | None
    net_bps_per_notional_mean: float
    count: int
    min_n: int
    safety_margin_bps: float
    oos_stable: bool
    requires_operator_signoff: bool
    cohort_key: str
    cohort_type: str
    reasoning: str
    oos_breakdown: list[dict[str, Any]] = field(default_factory=list)

    @property
    def recommends_change(self) -> bool:
        """True when the recommendation differs from the current configured mode."""
        return self.current_mode is not None and self.recommended_mode is not self.current_mode

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_mode": self.recommended_mode.value,
            "current_mode": (None if self.current_mode is None else self.current_mode.value),
            "recommends_change": self.recommends_change,
            "p_mu_net_positive": (
                None if self.p_mu_net_positive is None else round(self.p_mu_net_positive, 4)
            ),
            "net_bps_per_notional_mean": round(self.net_bps_per_notional_mean, 4),
            "count": self.count,
            "min_n": self.min_n,
            "safety_margin_bps": round(self.safety_margin_bps, 4),
            "oos_stable": self.oos_stable,
            "requires_operator_signoff": self.requires_operator_signoff,
            "cohort_key": self.cohort_key,
            "cohort_type": self.cohort_type,
            "reasoning": self.reasoning,
            "oos_breakdown": self.oos_breakdown,
        }


def assess_oos_stability(
    by_day: Sequence[CohortEdge],
    *,
    min_disjoint_days: int = DEFAULT_OOS_MIN_DISJOINT_DAYS,
    p_threshold: float = P_LIVE_LIMITED_MAX,
    safety_margin_bps: float = DEFAULT_SAFETY_MARGIN_BPS,
) -> tuple[bool, list[dict[str, Any]]]:
    """Deterministic out-of-sample stability check on disjoint daily cohorts.

    Definition (documented, not magic): edge is "out-of-sample stable" iff at
    least ``min_disjoint_days`` distinct day-cohorts INDEPENDENTLY clear BOTH the
    live posterior threshold (P >= ``p_threshold``) AND the net-edge margin
    (net > margin). Day cohorts are disjoint by construction (each trade belongs
    to exactly one UTC day in ``edge_report``), so two qualifying days are two
    genuinely separate samples — a minimal, honest train/holdout substitute.

    Days with ``p_mu_net_positive is None`` (insufficient) never count. Returns
    ``(stable, breakdown)`` where breakdown lists every day's qualifying status
    for operator transparency.
    """
    breakdown: list[dict[str, Any]] = []
    qualifying = 0
    for c in by_day:
        p = c.p_mu_net_positive
        net_ok = c.net_bps_per_notional_mean > safety_margin_bps
        p_ok = p is not None and p >= p_threshold
        passed = bool(p_ok and net_ok)
        if passed:
            qualifying += 1
        breakdown.append(
            {
                "day": c.cohort_key,
                "count": c.count,
                "p_mu_net_positive": (None if p is None else round(p, 4)),
                "net_bps_per_notional_mean": round(c.net_bps_per_notional_mean, 4),
                "qualifies": passed,
            }
        )
    stable = qualifying >= min_disjoint_days
    return stable, breakdown


def decide_release(
    cohort: CohortEdge,
    *,
    current_mode: EntryMode | None = None,
    min_n: int = DEFAULT_MIN_N,
    safety_margin_bps: float = DEFAULT_SAFETY_MARGIN_BPS,
    oos_stable: bool = False,
    oos_breakdown: Sequence[dict[str, Any]] | None = None,
) -> ReleaseDecision:
    """Map one ``CohortEdge`` to an ``EntryMode`` recommendation.

    Pure and deterministic. Does NOT touch settings, the loop, or the audit
    stream. Every hard invariant from the module docstring is enforced here and
    is independently unit-tested.

    ``oos_stable`` is supplied by the caller (typically ``assess_oos_stability``
    over the report's day cohorts); it only matters at the LIVE_NORMAL boundary.
    """
    p = cohort.p_mu_net_positive
    net = cohort.net_bps_per_notional_mean
    n = cohort.count
    breakdown = list(oos_breakdown or [])

    def build(mode: EntryMode, reason: str) -> ReleaseDecision:
        return ReleaseDecision(
            recommended_mode=mode,
            current_mode=current_mode,
            p_mu_net_positive=p,
            net_bps_per_notional_mean=net,
            count=n,
            min_n=min_n,
            safety_margin_bps=safety_margin_bps,
            oos_stable=oos_stable,
            requires_operator_signoff=mode.is_live,
            cohort_key=cohort.cohort_key,
            cohort_type=cohort.cohort_type,
            reasoning=reason,
            oos_breakdown=breakdown,
        )

    # Invariant 1: no defensible posterior -> DISABLED. Live is never auto-released
    # when the evidence cannot even support an edge-sign verdict.
    if p is None:
        return build(
            EntryMode.DISABLED,
            f"P(mu_net>0) insufficient (None) for cohort '{cohort.cohort_key}' "
            f"(n={n}). No defensible posterior -> DISABLED. Gather more closed "
            "round-trips before any release.",
        )
    if n < min_n:
        return build(
            EntryMode.DISABLED,
            f"n={n} < min_n={min_n} for cohort '{cohort.cohort_key}'. Sample too "
            f"small for a defensible release decision (P={p:.2%} not yet "
            "trustworthy) -> DISABLED.",
        )

    net_ok = net > safety_margin_bps

    # P below 0.50: the edge sign is not even more-likely-than-not positive.
    if p < P_DISABLED_MAX:
        return build(
            EntryMode.DISABLED,
            f"P(mu_net>0)={p:.2%} < {P_DISABLED_MAX:.0%}: cost-adjusted edge is "
            f"not more-likely-than-not positive (net={net:+.1f} bps/notional) "
            "-> DISABLED.",
        )

    # Invariant 2: positive-ish P but net does not beat the margin -> cap at PAPER.
    if not net_ok:
        return build(
            EntryMode.PAPER,
            f"P(mu_net>0)={p:.2%} but net={net:+.1f} bps/notional <= "
            f"margin={safety_margin_bps:+.1f}: no real cost-adjusted edge. "
            "Capped at PAPER (probability without edge magnitude is not tradable).",
        )

    if p < P_PAPER_MAX:
        return build(
            EntryMode.PAPER,
            f"P(mu_net>0)={p:.2%} in [{P_DISABLED_MAX:.0%}, {P_PAPER_MAX:.0%}), "
            f"net={net:+.1f} bps/notional > margin. Probe-grade evidence -> PAPER "
            "(keep gathering forward edge before any live cadence).",
        )

    if p < P_LIVE_LIMITED_MAX:
        return build(
            EntryMode.LIVE_LIMITED,
            f"P(mu_net>0)={p:.2%} in [{P_PAPER_MAX:.0%}, {P_LIVE_LIMITED_MAX:.0%}), "
            f"net={net:+.1f} bps/notional > margin. LIVE_LIMITED eligible with hard "
            "drawdown/churn caps - requires explicit operator sign-off; NOT "
            "auto-promoted.",
        )

    # P >= 0.95 and net > margin. LIVE_NORMAL only if also OOS-stable; never auto.
    if not oos_stable:
        return build(
            EntryMode.LIVE_LIMITED,
            f"P(mu_net>0)={p:.2%} >= {P_LIVE_LIMITED_MAX:.0%} and net={net:+.1f} "
            "bps/notional > margin, BUT out-of-sample stability NOT established "
            f"(need >= {DEFAULT_OOS_MIN_DISJOINT_DAYS} disjoint qualifying day "
            "cohorts). Cannot reach LIVE_NORMAL -> LIVE_LIMITED, operator sign-off "
            "required.",
        )
    return build(
        EntryMode.LIVE_NORMAL,
        f"P(mu_net>0)={p:.2%} >= {P_LIVE_LIMITED_MAX:.0%}, net={net:+.1f} "
        "bps/notional > margin, AND out-of-sample stable. LIVE_NORMAL ELIGIBLE - "
        "requires explicit operator promotion. The engine NEVER auto-promotes to "
        "live_normal; this is a recommendation, not an action.",
    )


def decide_from_report(
    report: Any,
    *,
    current_mode: EntryMode | None = None,
    min_n: int = DEFAULT_MIN_N,
    safety_margin_bps: float = DEFAULT_SAFETY_MARGIN_BPS,
    oos_min_disjoint_days: int = DEFAULT_OOS_MIN_DISJOINT_DAYS,
) -> ReleaseDecision:
    """Decide on the report's ``overall`` cohort, with OOS from its day cohorts.

    ``report`` is an ``app.observability.edge_report.EdgeReport``. Typed as
    ``Any`` to avoid a hard import cycle at call time; only ``.overall`` and
    ``.by_day`` (both ``CohortEdge``) are touched. Pure / read-only.
    """
    oos_stable, breakdown = assess_oos_stability(
        report.by_day,
        min_disjoint_days=oos_min_disjoint_days,
        p_threshold=P_LIVE_LIMITED_MAX,
        safety_margin_bps=safety_margin_bps,
    )
    return decide_release(
        report.overall,
        current_mode=current_mode,
        min_n=min_n,
        safety_margin_bps=safety_margin_bps,
        oos_stable=oos_stable,
        oos_breakdown=breakdown,
    )


# --- operator rendering (readable verdict, not JSON spam) ----------------------


def render_decision(decision: ReleaseDecision) -> str:
    """Render an operator-facing release verdict. Read-only; changes nothing."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("EDGE RELEASE DECISION (Sprint D - periodic, NOT a runtime gate)")
    lines.append("=" * 78)
    cur = "(unknown)" if decision.current_mode is None else decision.current_mode.value.upper()
    rec = decision.recommended_mode.value.upper()
    lines.append(f"  cohort:            {decision.cohort_key} ({decision.cohort_type})")
    lines.append(f"  current entry_mode: {cur}")
    lines.append(f"  RECOMMENDED:        {rec}")
    if decision.recommends_change:
        lines.append("                      ^ differs from current configuration")
    pv = decision.p_mu_net_positive
    p = "insufficient" if pv is None else f"{pv:.2%}"
    lines.append("")
    lines.append("  EVIDENCE")
    lines.append(f"    P(mu_net > 0)       = {p}")
    lines.append(f"    net bps/notional    = {decision.net_bps_per_notional_mean:+.1f}")
    lines.append(f"    count               = {decision.count} (min_n={decision.min_n})")
    lines.append(f"    safety_margin_bps   = {decision.safety_margin_bps:+.1f}")
    lines.append(f"    out-of-sample stable= {decision.oos_stable}")
    lines.append("")
    if decision.requires_operator_signoff:
        lines.append("  *** OPERATOR SIGN-OFF REQUIRED ***")
        lines.append("  This is a live recommendation. The engine does NOT promote")
        lines.append("  entry_mode. Promotion to any live mode is an explicit operator")
        lines.append("  action. LIVE_NORMAL is never auto-promoted.")
        lines.append("")
    lines.append("  REASONING")
    lines.append(f"    {decision.reasoning}")
    if decision.oos_breakdown:
        lines.append("")
        lines.append("  OOS DAY BREAKDOWN (disjoint daily cohorts)")
        for d in decision.oos_breakdown:
            dp_val = d["p_mu_net_positive"]
            dp = "insufficient" if dp_val is None else f"{dp_val:.2%}"
            mark = "PASS" if d["qualifies"] else "----"
            lines.append(
                f"    [{mark}] {d['day']:<12} n={d['count']:<4} "
                f"P={dp:<12} net={d['net_bps_per_notional_mean']:+.1f}"
            )
    return "\n".join(lines)
