"""LiveReadinessGate / ReleaseClassification — fail-closed live-trading preflight.

Why this exists
---------------
KAI must never drift into live trading by accident. The execution layer already
guards the *mechanism* (``ExecutionSettings`` validator: live needs explicit
flags). This module adds the *posture* answer on top: a single, read-only
classifier that says "what is the current release posture, and if it is not a
live candidate — exactly why, machine- and operator-readable".

Design invariants
-----------------
1. **Fail-closed.** With no evidence, the default classification is never a live
   candidate. ``classify_release()`` only returns a ``live_*_candidate`` when
   *every* hard gate passes. Missing evidence == unmet gate == blocker.
2. **Pure / read-only.** No settings mutation, no execution, no IO in the core
   classifier. ``default_ignored_mypy_modules()`` is the one optional reader.
3. **Negative or unproven edge is a hard live-blocker.** Low fees are not edge.
   Live needs a positive cost-adjusted ``net_edge_bps`` *and* an out-of-sample
   confirmation, else the posture is paper-only/blocked.
4. **mypy-ignored trading-core modules are live-blockers** — ``ignore_errors``
   has demonstrably masked real bugs (e.g. the kai_chat_engine persona bug,
   the youtube ``list_transcripts`` breakage), so a still-ignored trading-core
   module means unverified type safety on a money path.

This module is itself strict-typed and intentionally NOT in the mypy
ignore_errors override.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.enums import ExecutionMode

# Trading-critical modules. While any of these is mypy-ignored, live trading is
# blocked: their type safety on the order/PnL/audit path is unverified.
TRADING_CRITICAL_MODULES: frozenset[str] = frozenset(
    {
        "app.execution.paper_engine",
        "app.execution.envelope_to_paper_bridge",
        "app.execution.audit_replay",
        "app.execution.operator_entry_watch",
        "app.execution.portfolio_read",
        "app.messaging.signal_parser",
        "app.market_data.binance_adapter",
        "app.api.routers.signals",
    }
)

# Cost-adjusted edge safety margin (bps) applied on top of explicit costs.
SAFETY_MARGIN_BPS_DEFAULT: float = 5.0

# Minimum cost-adjusted net edge (bps) required for a live candidate. A marginal
# positive net edge below this is "below live threshold", not "negative" — the
# distinction matters for honest diagnosis (low fees are not edge either).
LIVE_EDGE_THRESHOLD_BPS_DEFAULT: float = 0.0

# Minimum posterior win-probability for a live candidate. e.g. a paper posterior
# of ~0.546 is too weak to risk live cadence.
MIN_POSTERIOR_PROB_DEFAULT: float = 0.55

# Full catalogue of blocker codes this gate can emit. Lets consumers separate the
# *possible* codes (this set) from the *active* ones (ReleaseStatus.blockers).
POSSIBLE_BLOCKER_CODES: frozenset[str] = frozenset(
    {
        "MYPY_TRADING_CORE_IGNORED",
        "COST_MODEL_NOT_SSOT",
        "PNL_FEE_NOT_SEPARATED",
        "EDGE_UNPROVEN",
        "EDGE_BELOW_LIVE_THRESHOLD",
        "EDGE_POSTERIOR_TOO_WEAK",
        "EDGE_NOT_OOS_CONFIRMED",
        "CHURN_LIMITS_INACTIVE",
        "REMOTE_CI_NOT_GREEN",
        "LIVE_ENABLEMENT_UNGUARDED",
        "OPERATOR_APPROVAL_MISSING",
        "PAPER_CONFIG_CONTRADICTORY",
    }
)


class ReleaseClassification(StrEnum):
    """Coarse release posture. Ordered conceptually from safest to most-capable."""

    OPERATOR_PAPER_READY = "operator_paper_ready"
    PAPER_ONLY = "paper_only"
    LIVE_BLOCKED = "live_blocked"
    LIVE_LIMITED_CANDIDATE = "live_limited_candidate"
    LIVE_NORMAL_CANDIDATE = "live_normal_candidate"


@dataclass(frozen=True)
class Blocker:
    """A single unmet condition. ``code`` is machine-readable, ``message`` human."""

    code: str
    gate: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "gate": self.gate, "message": self.message}


@dataclass(frozen=True)
class OperatorCommsConfig:
    """Operator-communication contract (goal §6). Declarative — the harness /
    block-recap hook enforces the no-recap behaviour; this records the intent."""

    recaps_enabled: bool = False
    operator_memos_enabled: bool = True
    final_status_summary_enabled: bool = True


OPERATOR_COMMS = OperatorCommsConfig()


@dataclass(frozen=True)
class LiveReadinessEvidence:
    """Evidence inputs for live candidacy. Every field defaults to the
    fail-closed value (False / None) so an empty instance yields a blocked
    posture. ``net_edge_bps is None`` means "no posterior / report" -> blocker."""

    cost_model_single_source: bool = False
    pnl_fee_separation: bool = False
    net_edge_bps: float | None = None
    # Posterior win-probability behind the edge. None == not on record.
    posterior_prob: float | None = None
    churn_limits_active: bool = False
    remote_ci_green: bool = False
    out_of_sample_edge_positive: bool = False
    operator_live_approval: bool = False
    # When None, trading-core type safety is derived from the ignored-module list.
    trading_core_mypy_clean: bool | None = None
    # True caps candidacy at live_limited_candidate even if all gates pass.
    limited_caps_only: bool = False


@dataclass(frozen=True)
class ExecutionPosture:
    """Minimal read-only view of execution settings the classifier needs."""

    mode: ExecutionMode
    live_enabled: bool
    dry_run: bool
    approval_required: bool

    @classmethod
    def from_settings(cls, execution: Any) -> ExecutionPosture:
        """Build from an ``ExecutionSettings`` (duck-typed to avoid import cycle)."""
        return cls(
            mode=execution.mode,
            live_enabled=bool(execution.live_enabled),
            dry_run=bool(execution.dry_run),
            approval_required=bool(execution.approval_required),
        )


@dataclass(frozen=True)
class ReleaseStatus:
    """Result of :func:`classify_release` — machine- and operator-readable."""

    classification: ReleaseClassification
    blockers: tuple[Blocker, ...]
    hard_gates: dict[str, bool] = field(default_factory=dict)

    @property
    def is_live_candidate(self) -> bool:
        return self.classification in (
            ReleaseClassification.LIVE_LIMITED_CANDIDATE,
            ReleaseClassification.LIVE_NORMAL_CANDIDATE,
        )

    @property
    def live_blocked(self) -> bool:
        return not self.is_live_candidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "is_live_candidate": self.is_live_candidate,
            "hard_gates": dict(self.hard_gates),
            # active_blockers = currently failing; possible_blocker_codes = full
            # catalogue. A code in the catalogue is NOT a current blocker unless
            # it also appears in active_blockers (e.g. REMOTE_CI_NOT_GREEN is only
            # active when remote CI is not confirmed green).
            "active_blockers": [b.to_dict() for b in self.blockers],
            "active_blocker_codes": sorted(b.code for b in self.blockers),
            "possible_blocker_codes": sorted(POSSIBLE_BLOCKER_CODES),
        }


def compute_net_edge_bps(
    side_adjusted_return_bps: float,
    fees_bps: float,
    spread_bps: float,
    slippage_bps: float,
    safety_margin_bps: float = SAFETY_MARGIN_BPS_DEFAULT,
) -> float:
    """net_edge = side_adjusted_return - fees - spread - slippage - safety_margin.

    Low fees are not edge: a strategy with positive gross but costs above it has
    negative net edge and must stay blocked.
    """
    return side_adjusted_return_bps - fees_bps - spread_bps - slippage_bps - safety_margin_bps


def default_ignored_mypy_modules(
    pyproject_path: Path = Path("pyproject.toml"),
) -> tuple[str, ...]:
    """Read the current ``[[tool.mypy.overrides]]`` ignore_errors module list.

    Best-effort: returns an empty tuple if the file or section is missing. Used
    by API/CLI surfaces; the pure classifier takes the list as a parameter.
    """
    try:
        import tomllib

        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    overrides = data.get("tool", {}).get("mypy", {}).get("overrides", [])
    modules: list[str] = []
    for override in overrides:
        if override.get("ignore_errors"):
            mods = override.get("module", [])
            if isinstance(mods, str):
                mods = [mods]
            modules.extend(str(m) for m in mods)
    return tuple(modules)


def classify_release(
    posture: ExecutionPosture,
    evidence: LiveReadinessEvidence | None = None,
    *,
    ignored_mypy_modules: Sequence[str] = (),
    live_edge_threshold_bps: float = LIVE_EDGE_THRESHOLD_BPS_DEFAULT,
    min_posterior_prob: float = MIN_POSTERIOR_PROB_DEFAULT,
) -> ReleaseStatus:
    """Classify the current release posture, fail-closed.

    Returns a :class:`ReleaseStatus` whose ``classification`` is only a
    ``live_*_candidate`` when every hard gate passes; otherwise it is
    ``operator_paper_ready`` / ``paper_only`` / ``live_blocked`` with the unmet
    gates listed as machine-readable blockers.
    """
    ev = evidence or LiveReadinessEvidence()
    blockers: list[Blocker] = []
    gates: dict[str, bool] = {}

    def gate(name: str, ok: bool, code: str, message: str) -> bool:
        gates[name] = ok
        if not ok:
            blockers.append(Blocker(code=code, gate=name, message=message))
        return ok

    # --- Trading-core type safety -------------------------------------------
    if ev.trading_core_mypy_clean is None:
        ignored = set(ignored_mypy_modules)
        offending = sorted(TRADING_CRITICAL_MODULES & ignored)
        mypy_clean = not offending
        mypy_msg = (
            "trading-core mypy clean"
            if mypy_clean
            else f"trading-critical modules still mypy-ignored: {', '.join(offending)}"
        )
    else:
        mypy_clean = ev.trading_core_mypy_clean
        mypy_msg = (
            "trading-core mypy clean (asserted)"
            if mypy_clean
            else "trading-core mypy not clean (asserted)"
        )
    gate("trading_core_mypy_clean", mypy_clean, "MYPY_TRADING_CORE_IGNORED", mypy_msg)

    # --- Cost model + accounting --------------------------------------------
    gate(
        "cost_model_single_source",
        ev.cost_model_single_source,
        "COST_MODEL_NOT_SSOT",
        "CostModel is not the single source of truth for engine/backtest/gate/reporting",
    )
    gate(
        "pnl_fee_separation",
        ev.pnl_fee_separation,
        "PNL_FEE_NOT_SEPARATED",
        "closed PnL / open MTM / fees_closed / fees_open are not separated",
    )

    # --- Edge (unproven / below-threshold / weak-posterior are hard blockers).
    # Precise codes prevent later misdiagnosis: a marginal positive edge below the
    # live threshold is NOT "negative" — and low fees are not edge.
    if ev.net_edge_bps is None:
        gate(
            "net_edge_meets_threshold",
            False,
            "EDGE_UNPROVEN",
            "no cost-adjusted EdgeGate report — net_edge_bps is unknown",
        )
    else:
        gate(
            "net_edge_meets_threshold",
            ev.net_edge_bps > live_edge_threshold_bps,
            "EDGE_BELOW_LIVE_THRESHOLD",
            (
                f"cost-adjusted net_edge_bps={ev.net_edge_bps} is not > live threshold "
                f"{live_edge_threshold_bps} (low fees are not edge)"
            ),
        )
    posterior_ok = ev.posterior_prob is not None and ev.posterior_prob >= min_posterior_prob
    gate(
        "posterior_meets_min",
        posterior_ok,
        "EDGE_POSTERIOR_TOO_WEAK",
        (
            "no posterior win-probability on record"
            if ev.posterior_prob is None
            else f"posterior win-prob {ev.posterior_prob} < required {min_posterior_prob}"
        ),
    )
    gate(
        "out_of_sample_edge_positive",
        ev.out_of_sample_edge_positive,
        "EDGE_NOT_OOS_CONFIRMED",
        "paper/shadow report does not show positive cost-adjusted edge out-of-sample",
    )

    # --- Churn / cooldown ----------------------------------------------------
    gate(
        "churn_limits_active",
        ev.churn_limits_active,
        "CHURN_LIMITS_INACTIVE",
        "V2 cooldown / churn limits are not active",
    )

    # --- Remote CI -----------------------------------------------------------
    gate(
        "remote_ci_green",
        ev.remote_ci_green,
        "REMOTE_CI_NOT_GREEN",
        "remote CI is not confirmed green (local gates are insufficient)",
    )

    # --- Live enablement is explicitly guarded ------------------------------
    if posture.mode is ExecutionMode.LIVE:
        guarded = posture.live_enabled and not posture.dry_run and posture.approval_required
        gate(
            "live_enablement_guarded",
            guarded,
            "LIVE_ENABLEMENT_UNGUARDED",
            "EXECUTION_MODE=live without the full live_enabled/!dry_run/approval guard set",
        )
    else:
        gates["live_enablement_guarded"] = True

    # --- Operator sign-off ---------------------------------------------------
    gate(
        "operator_live_approval",
        ev.operator_live_approval,
        "OPERATOR_APPROVAL_MISSING",
        "explicit operator live approval is not on record",
    )

    # --- Paper configuration sanity (contradictions are a hard block) -------
    paper_contradiction = posture.live_enabled and posture.mode is not ExecutionMode.LIVE
    if not paper_contradiction and posture.mode is not ExecutionMode.LIVE and not posture.dry_run:
        # non-live mode must run dry — otherwise the config is internally unsafe
        paper_contradiction = True
    paper_healthy = not paper_contradiction

    all_live_gates_pass = all(gates.values())
    live_attempted = posture.mode is ExecutionMode.LIVE or posture.live_enabled

    # --- Classify (fail-closed) ---------------------------------------------
    classification: ReleaseClassification
    if not paper_healthy:
        classification = ReleaseClassification.LIVE_BLOCKED
        blockers.append(
            Blocker(
                code="PAPER_CONFIG_CONTRADICTORY",
                gate="paper_config_sane",
                message=(
                    "execution config is internally contradictory (e.g. live_enabled "
                    "without LIVE mode, or non-live without dry_run)"
                ),
            )
        )
    elif all_live_gates_pass:
        classification = (
            ReleaseClassification.LIVE_LIMITED_CANDIDATE
            if ev.limited_caps_only
            else ReleaseClassification.LIVE_NORMAL_CANDIDATE
        )
    elif live_attempted:
        # live was configured/attempted but gates failed -> fail-closed
        classification = ReleaseClassification.LIVE_BLOCKED
    elif posture.approval_required:
        classification = ReleaseClassification.OPERATOR_PAPER_READY
    else:
        classification = ReleaseClassification.PAPER_ONLY

    return ReleaseStatus(
        classification=classification,
        blockers=tuple(blockers),
        hard_gates=gates,
    )
