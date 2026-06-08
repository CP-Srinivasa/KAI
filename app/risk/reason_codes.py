"""Canonical reason-code vocabulary for the risk / signal-execution pipeline.

Why this module exists
----------------------
The risk engine historically emitted ad-hoc human-readable violation strings
(``max_open_positions_reached:6>=6``). Those are great for a human reading a log
line, but they are NOT a stable contract: the UI, alerts, metrics and the
operator runbook all need a small, stable, machine-grade vocabulary that does
not change when someone tweaks the wording of a violation message.

Design contract
---------------
- The human violation strings stay exactly as they are (backward-compatible —
  existing tests and audit consumers assert on them). This module is **additive**:
  it maps each violation to a stable ``REJECT_*`` code.
- ``map_violation_to_code`` is total: an unknown violation maps to
  ``REJECT_UNCLASSIFIED`` rather than raising — a missing mapping must never
  crash the risk path (fail-soft on classification, fail-closed on the gate).
- Final lifecycle states are a closed enum so the UI/observability can render a
  signal's terminal disposition without string-sniffing.

This module has **no imports from app.*** on purpose: it is a leaf so any layer
(risk, execution, observability, api) can depend on it without cycles.
"""

from __future__ import annotations

from enum import StrEnum

# --------------------------------------------------------------------------- #
# Reject reason codes — stable machine vocabulary.
# --------------------------------------------------------------------------- #


class RejectCode(StrEnum):
    """Stable reject codes. The string value is the wire/audit/UI contract."""

    # Hard system gates
    KILL_SWITCH = "REJECT_KILL_SWITCH"
    SYSTEM_PAUSED = "REJECT_SYSTEM_PAUSED"

    # Position-management gates
    AVERAGING_DOWN = "REJECT_AVERAGING_DOWN"
    MARTINGALE = "REJECT_MARTINGALE"
    MAX_OPEN_POSITIONS = "REJECT_MAX_OPEN_POSITIONS"

    # Stop-loss / geometry gates
    STOP_LOSS_MISSING = "REJECT_STOP_LOSS_MISSING"
    SL_GEOMETRY = "REJECT_SL_GEOMETRY"
    TP_GEOMETRY = "REJECT_TP_GEOMETRY"
    SUB_COST_GEOMETRY = "REJECT_SUB_COST_GEOMETRY"

    # Signal-quality gates
    CONFIDENCE_TOO_LOW = "REJECT_CONFIDENCE_TOO_LOW"
    CONFLUENCE_TOO_LOW = "REJECT_CONFLUENCE_TOO_LOW"

    # Reward/risk + risk-budget gates (Sprint 2026-06-02)
    RR_TOO_LOW = "REJECT_RR_TOO_LOW"
    AVG_RR_TOO_LOW = "REJECT_AVG_RR_TOO_LOW"
    RISK_TOO_HIGH = "REJECT_RISK_TOO_HIGH"
    NET_EDGE_TOO_LOW = "REJECT_NET_EDGE_TOO_LOW"
    TARGET_TOO_CLOSE = "REJECT_TARGET_TOO_CLOSE"

    # Portfolio-state gates
    DAILY_LOSS = "REJECT_DAILY_LOSS"
    DRAWDOWN = "REJECT_DRAWDOWN"
    REGIME_CONFLICT = "REJECT_REGIME_CONFLICT"

    # Sizing / notional
    NOTIONAL_TOO_LOW = "REJECT_NOTIONAL_TOO_LOW"
    POSITION_TOO_LARGE = "REJECT_POSITION_TOO_LARGE"

    # Exchange preflight (Sprint 2026-06-02, see app/execution/exchange_preflight.py)
    INVALID_TICK_SIZE = "REJECT_INVALID_TICK_SIZE"
    EXCHANGE_FILTER = "REJECT_EXCHANGE_FILTER"

    # Ingress / parser
    REJECTED_BY_PARSER = "REJECT_BY_PARSER"

    # Source / allowlist
    SOURCE_NOT_ALLOWLISTED = "REJECT_SOURCE_NOT_ALLOWLISTED"

    # Fallback — keeps the mapper total.
    UNCLASSIFIED = "REJECT_UNCLASSIFIED"


class ExecutionBlockerCode(StrEnum):
    """Execution-level blockers — DISTINCT from risk-gate ``RejectCode``.

    These are NOT signal-quality / geometry rejects. They are global mode
    blockers that override an otherwise-approvable signal. Kept separate so an
    operator/report never confuses "the risk gate rejected this" with "the
    global entry-mode kill-switch refused to act on it". The string value is the
    wire/audit/UI contract.
    """

    # EXECUTION_ENTRY_MODE=disabled — global kill-switch on risk-increasing
    # entries (autonomous loop AND premium/promoted bridge). Exits are never
    # blocked by this.
    ENTRY_MODE_DISABLED = "ENTRY_MODE_DISABLED"

    # Premium-Fastlane wanted to bypass entry_mode=disabled for the paper route
    # but the two-flag override (bypass_entry_mode_for_paper +
    # allow_entry_mode_disabled_override) was not fully armed (Issue #181). The
    # kill-switch held — recorded so the dashboard shows the fail-closed refusal.
    FASTLANE_ENTRY_MODE_OVERRIDE_NOT_ARMED = "FASTLANE_ENTRY_MODE_OVERRIDE_NOT_ARMED"


class FinalStatus(StrEnum):
    """Closed set of terminal dispositions for a signal/order in the pipeline.

    The UI renders these directly; observability aggregates on them. Anything
    that is not provably one of the deterministic terminal states must surface
    as ``UNKNOWN_REQUIRES_RECONCILIATION`` — never silently as success.
    """

    EXECUTED = "EXECUTED"
    REJECTED_WITH_REASON = "REJECTED_WITH_REASON"
    EXPIRED = "EXPIRED"
    QUARANTINED = "QUARANTINED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    UNKNOWN_REQUIRES_RECONCILIATION = "UNKNOWN_REQUIRES_RECONCILIATION"


# --------------------------------------------------------------------------- #
# Violation-prefix -> RejectCode mapping.
# --------------------------------------------------------------------------- #
# The engine emits ``"<prefix>:<detail>"`` (or a bare ``"<prefix>"``). We key on
# the prefix (text before the first ':'). Keep this table in lock-step with the
# violation strings appended in app/risk/engine.py.

_PREFIX_TO_CODE: dict[str, RejectCode] = {
    "kill_switch_active": RejectCode.KILL_SWITCH,
    "system_paused": RejectCode.SYSTEM_PAUSED,
    "averaging_down_not_allowed": RejectCode.AVERAGING_DOWN,
    "martingale_not_allowed": RejectCode.MARTINGALE,
    "stop_loss_required_but_missing": RejectCode.STOP_LOSS_MISSING,
    "sl_geometry_invalid": RejectCode.SL_GEOMETRY,
    "tp_geometry_invalid": RejectCode.TP_GEOMETRY,
    "sub_cost_geometry_rejected": RejectCode.SUB_COST_GEOMETRY,
    "signal_confidence_too_low": RejectCode.CONFIDENCE_TOO_LOW,
    "signal_confluence_too_low": RejectCode.CONFLUENCE_TOO_LOW,
    "max_open_positions_reached": RejectCode.MAX_OPEN_POSITIONS,
    "daily_loss_limit_breached": RejectCode.DAILY_LOSS,
    "drawdown_limit_breached": RejectCode.DRAWDOWN,
    "regime_conflict": RejectCode.REGIME_CONFLICT,
    # Sprint 2026-06-02 reward/risk gates
    "rr_too_low": RejectCode.RR_TOO_LOW,
    "avg_rr_too_low": RejectCode.AVG_RR_TOO_LOW,
    "signal_risk_too_high": RejectCode.RISK_TOO_HIGH,
    "leveraged_risk_too_high": RejectCode.RISK_TOO_HIGH,
    "net_edge_too_low": RejectCode.NET_EDGE_TOO_LOW,
    "target_too_close": RejectCode.TARGET_TOO_CLOSE,
    # Sizing
    "notional_below_min": RejectCode.NOTIONAL_TOO_LOW,
    "position_size_exceeds_cap": RejectCode.POSITION_TOO_LARGE,
}


def violation_prefix(violation: str) -> str:
    """Return the stable prefix token of a violation string."""
    return violation.split(":", 1)[0].strip()


def map_violation_to_code(violation: str) -> RejectCode:
    """Map a single violation string to its stable RejectCode.

    Total function: unknown prefixes return ``REJECT_UNCLASSIFIED`` so a new,
    not-yet-mapped violation degrades observability gracefully instead of
    crashing the risk path.
    """
    return _PREFIX_TO_CODE.get(violation_prefix(violation), RejectCode.UNCLASSIFIED)


def map_violations_to_codes(violations: list[str]) -> list[str]:
    """Map a list of violations to a de-duplicated, order-preserving list of codes."""
    seen: set[str] = set()
    out: list[str] = []
    for v in violations:
        code = map_violation_to_code(v).value
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


__all__ = [
    "ExecutionBlockerCode",
    "FinalStatus",
    "RejectCode",
    "map_violation_to_code",
    "map_violations_to_codes",
    "violation_prefix",
]
