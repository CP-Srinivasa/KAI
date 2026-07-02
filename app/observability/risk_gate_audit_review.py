"""Risk-gate audit-window REVIEW — read-only enforce-readiness assessment.

This is the analysis layer on top of ``risk_gate_audit`` (which records flagged
``would_reject`` evals while ``RISK_GATES_MODE=audit``). It answers ONE question
for the operator: *is there enough evidence to consider flipping a reward/risk
gate to enforce?* — and it NEVER changes anything (no enforce, no entry_mode
flip, no orders).

Sample-size staging (operator decision rule, 2026-06-02):
  n == 0              -> NO_DATA          (extend window, no enforce)
  0  < n < 10         -> INSUFFICIENT_DATA (extend window, no enforce)
  10 <= n < 30        -> LOW_SAMPLE        (descriptive only, no enforce)
  n >= 30             -> REVIEWABLE        (human review required before enforce)

``n`` is the number of premium/promoted signals that actually reached the risk
gate (the denominator), derived from the bridge audit — NOT just the flagged
count. Empty data is a STATUS, never a hard error: a quiet window must not raise
a false alarm or block.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.risk_gate_audit import build_risk_gate_audit_report

_DEFAULT_BRIDGE = Path("artifacts/bridge_pending_orders.jsonl")

# Bridge stages that mean a signal reached/passed the risk gate (gate 5). Stages
# strictly BEFORE the gate (e.g. skipped_source, entry-not-reached) are excluded
# so the denominator counts only genuinely evaluated signals.
_POST_GATE_STAGES = frozenset(
    {
        "rejected_risk",
        "rejected_size",
        "rejected_fill",
        "rejected_incomplete",
        "rejected_position_exists",
        "rejected_scale_review",
        "rejected_entry_mode",
        "pending",
        "filled",
        "filled_duplicate_suppressed",
        "expired",
    }
)


@dataclass
class ReviewVerdict:
    generated_at: str
    status: str  # NO_DATA | INSUFFICIENT_DATA | LOW_SAMPLE | REVIEWABLE
    n_evaluated: int
    would_reject_count: int
    reject_rate: float | None
    reason_code_distribution: dict[str, int]
    rejected_by_symbol: dict[str, int]
    rejected_by_source: dict[str, int]
    enforce_ready: bool
    decision: str
    preconditions: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "n_evaluated": self.n_evaluated,
            "would_reject_count": self.would_reject_count,
            "reject_rate": self.reject_rate,
            "reason_code_distribution": dict(self.reason_code_distribution),
            "rejected_by_symbol": dict(self.rejected_by_symbol),
            "rejected_by_source": dict(self.rejected_by_source),
            "enforce_ready": self.enforce_ready,
            "decision": self.decision,
            "preconditions": dict(self.preconditions),
            "notes": list(self.notes),
        }


def _count_evaluated_signals(bridge_path: Path) -> int:
    """Distinct envelopes whose latest stage shows they reached the risk gate."""
    if not bridge_path.exists():
        return 0
    try:
        lines = bridge_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    latest_stage: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        env_id = rec.get("envelope_id")
        stage = rec.get("stage")
        if isinstance(env_id, str) and isinstance(stage, str):
            latest_stage[env_id] = stage
    return sum(1 for s in latest_stage.values() if s in _POST_GATE_STAGES)


def _classify(n: int) -> tuple[str, str]:
    if n == 0:
        return "NO_DATA", "no enforce — extend observation window (no signals evaluated yet)"
    if n < 10:
        return (
            "INSUFFICIENT_DATA",
            "no enforce — extend observation window (sample too small)",
        )
    if n < 30:
        return "LOW_SAMPLE", "no enforce — descriptive only (sample < 30)"
    return (
        "REVIEWABLE",
        "no automatic enforce — human review + explicit operator sign-off required",
    )


def build_review(
    *,
    entry_mode: str | None = None,
    gates_mode: str | None = None,
    max_leveraged_risk_pct: float | None = None,
    min_rr: float | None = None,
    audit_log_path: str | Path | None = None,
    bridge_path: str | Path | None = None,
    now: datetime | None = None,
) -> ReviewVerdict:
    """Build the read-only enforce-readiness verdict. Never mutates state."""
    report = build_risk_gate_audit_report(log_path=audit_log_path)
    n_eval = _count_evaluated_signals(Path(bridge_path) if bridge_path else _DEFAULT_BRIDGE)
    would_reject = report.would_reject_count
    reject_rate = round(would_reject / n_eval, 4) if n_eval > 0 else None

    status, decision = _classify(n_eval)

    preconditions = {
        "entry_mode": entry_mode,
        "entry_mode_disabled": (entry_mode == "disabled") if entry_mode is not None else None,
        "gates_mode": gates_mode,
        "gates_mode_audit": (gates_mode == "audit") if gates_mode is not None else None,
        "max_leveraged_risk_pct": max_leveraged_risk_pct,
        "min_rr": min_rr,
    }

    notes: list[str] = [
        "READ-ONLY review. No enforce, no entry_mode change, no orders/fills.",
        "n_evaluated = signals that reached the risk gate (bridge denominator); "
        "would_reject_count = flagged audit evals. reject_rate = flagged / evaluated.",
        "False-positive identification requires per-signal outcome correlation "
        "(alert_outcomes) and human judgement — not auto-computed here.",
    ]
    if gates_mode is not None and gates_mode != "audit":
        notes.append(f"WARNING: gates_mode={gates_mode} (expected 'audit' for this window).")
    if entry_mode is not None and entry_mode != "disabled":
        notes.append(
            f"NOTE: entry_mode={entry_mode} (operator-controlled; review does not change it)."
        )

    # enforce_ready is ALWAYS False here — flipping to enforce is a separate,
    # explicit operator decision. This field exists so downstream/UI never
    # mistakes a REVIEWABLE status for an automatic green light.
    return ReviewVerdict(
        generated_at=(now or datetime.now(UTC)).isoformat(),
        status=status,
        n_evaluated=n_eval,
        would_reject_count=would_reject,
        reject_rate=reject_rate,
        reason_code_distribution=report.reason_code_distribution,
        rejected_by_symbol=report.rejected_by_symbol,
        rejected_by_source=report.rejected_by_source,
        enforce_ready=False,
        decision=decision,
        preconditions=preconditions,
        notes=notes,
    )


def render_markdown(v: ReviewVerdict) -> str:
    lines = [
        f"# Risk-Gate Audit Review — {v.generated_at}",
        "",
        f"**Status:** {v.status}  |  **enforce-ready: {'YES' if v.enforce_ready else 'NO'}**",
        f"**Decision:** {v.decision}",
        "",
        f"- signals evaluated (n): {v.n_evaluated}",
        f"- would_reject (flagged): {v.would_reject_count}",
        f"- reject_rate: {v.reject_rate if v.reject_rate is not None else 'n/a'}",
        "",
        "## Preconditions",
        f"- entry_mode: {v.preconditions.get('entry_mode')} "
        f"(disabled={v.preconditions.get('entry_mode_disabled')})",
        f"- gates_mode: {v.preconditions.get('gates_mode')} "
        f"(audit={v.preconditions.get('gates_mode_audit')})",
        f"- RISK_MAX_LEVERAGED_RISK_PCT: {v.preconditions.get('max_leveraged_risk_pct')}",
        f"- RISK_MIN_RR: {v.preconditions.get('min_rr')}",
        "",
        "## Reason-code distribution",
    ]
    if v.reason_code_distribution:
        lines += [f"- {code}: {cnt}" for code, cnt in v.reason_code_distribution.items()]
    else:
        lines.append("- (none)")
    lines += ["", "## By symbol"]
    lines += (
        [f"- {s}: {c}" for s, c in v.rejected_by_symbol.items()]
        if v.rejected_by_symbol
        else ["- (none)"]
    )
    lines += ["", "## By source"]
    lines += (
        [f"- {s}: {c}" for s, c in v.rejected_by_source.items()]
        if v.rejected_by_source
        else ["- (none)"]
    )
    lines += ["", "## Notes"] + [f"- {n}" for n in v.notes]
    return "\n".join(lines)


def render_telegram(v: ReviewVerdict) -> str:
    top = ", ".join(f"{c}={n}" for c, n in list(v.reason_code_distribution.items())[:3]) or "-"
    return "\n".join(
        [
            "KAI Risk-Gate Audit Review",
            f"status: {v.status}  (enforce-ready: NO)",
            f"n_evaluated: {v.n_evaluated}  would_reject: {v.would_reject_count}",
            f"reject_rate: {v.reject_rate if v.reject_rate is not None else 'n/a'}",
            f"top codes: {top}",
            f"decision: {v.decision}",
            "entry_mode stays disabled; gates stay audit.",
        ]
    )


__all__ = [
    "ReviewVerdict",
    "build_review",
    "render_markdown",
    "render_telegram",
]
