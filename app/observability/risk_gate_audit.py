"""Risk-gate audit trail + report — the staged-rollout safety net.

The Gate-10 reward/risk gates ship default-OFF and, when a threshold is set,
default to ``audit`` mode (see ``RiskSettings.gates_mode``). In audit mode the
gate computes ``would_reject`` but does NOT block. This module persists those
evaluations to ``artifacts/risk_gate_audit.jsonl`` and aggregates them into a
report so an operator can measure reject-rate and false-positive risk BEFORE
flipping to ``enforce`` — the direct countermeasure to a silent
book-starvation incident.

Write path is best-effort/fail-soft: an audit write must never break the bridge.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.risk.models import RiskCheckResult

logger = logging.getLogger(__name__)

_DEFAULT_LOG = Path("artifacts/risk_gate_audit.jsonl")


def record_risk_gate_eval(
    *,
    risk_result: RiskCheckResult,
    envelope_id: str | None = None,
    correlation_id: str | None = None,
    source: str | None = None,
    symbol: str | None = None,
    enforced: bool = False,
    now: datetime | None = None,
    log_path: Path | None = None,
) -> bool:
    """Append one risk-gate evaluation IF the reward/risk gate flagged it.

    Returns True when a record was written. No-op (returns False) when the gate
    did not flag the signal — keeps the audit file focused on the interesting
    cases. Fail-soft on IO error.
    """
    if not risk_result.would_reject:
        return False
    gates_mode = risk_result.details.get("gates_mode")
    record = {
        "timestamp_utc": (now or datetime.now(UTC)).isoformat(),
        "event": "risk_gate_audit",
        "envelope_id": envelope_id,
        "correlation_id": correlation_id,
        "source": source,
        "symbol": symbol or risk_result.symbol,
        "gates_mode": gates_mode,
        "enforced": enforced,
        "would_reject": True,
        "would_reject_codes": list(risk_result.would_reject_codes),
        "would_reject_violations": list(risk_result.would_reject_violations),
        "signal_geometry": risk_result.details.get("signal_geometry"),
        "risk_check_id": risk_result.check_id,
    }
    path = log_path or _DEFAULT_LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        logger.error("[risk-gate-audit] write failed: %s", exc)
        return False


@dataclass
class RiskGateAuditReport:
    total_records: int
    would_reject_count: int
    reject_rate: float
    reason_code_distribution: dict[str, int]
    rejected_by_symbol: dict[str, int]
    rejected_by_source: dict[str, int]
    enforced_count: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total_records,
            "would_reject_count": self.would_reject_count,
            "reject_rate": self.reject_rate,
            "reason_code_distribution": dict(self.reason_code_distribution),
            "rejected_by_symbol": dict(self.rejected_by_symbol),
            "rejected_by_source": dict(self.rejected_by_source),
            "enforced_count": self.enforced_count,
            "notes": list(self.notes),
        }


def build_risk_gate_audit_report(*, log_path: str | Path | None = None) -> RiskGateAuditReport:
    """Aggregate the risk-gate audit JSONL.

    ``total_records`` counts the audit lines (all of which are would_reject
    flags, since only flagged evals are written). The report's value is the
    distribution: which codes dominate, which symbols/sources, and how many are
    already in enforce mode — the inputs to the enforce-readiness decision.
    """
    path = Path(log_path) if log_path else _DEFAULT_LOG
    code_dist: Counter[str] = Counter()
    by_symbol: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    total = 0
    would_reject = 0
    enforced = 0
    notes: list[str] = []

    if not path.exists():
        return RiskGateAuditReport(0, 0, 0.0, {}, {}, {}, 0, ["no audit file yet"])

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return RiskGateAuditReport(0, 0, 0.0, {}, {}, {}, 0, [f"read failed: {exc}"])

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1
        if rec.get("would_reject"):
            would_reject += 1
        if rec.get("enforced"):
            enforced += 1
        for code in rec.get("would_reject_codes", []) or []:
            code_dist[str(code)] += 1
        sym = rec.get("symbol")
        if isinstance(sym, str):
            by_symbol[sym] += 1
        src = rec.get("source")
        if isinstance(src, str):
            by_source[src] += 1

    reject_rate = (would_reject / total) if total else 0.0
    notes.append(
        "would_reject_count is the count of flagged evals; pair with the total "
        "signals processed (bridge audit) to get the true population reject-rate. "
        "accepted_but_later_bad / rejected_but_would_have_been_good require "
        "outcome correlation (alert_outcomes) — not computed here."
    )
    return RiskGateAuditReport(
        total_records=total,
        would_reject_count=would_reject,
        reject_rate=round(reject_rate, 4),
        reason_code_distribution=dict(code_dist.most_common()),
        rejected_by_symbol=dict(by_symbol.most_common()),
        rejected_by_source=dict(by_source.most_common()),
        enforced_count=enforced,
        notes=notes,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="Risk-gate audit report (audit-mode readiness)")
    ap.add_argument("--log", default=str(_DEFAULT_LOG))
    args = ap.parse_args(argv)
    report = build_risk_gate_audit_report(log_path=args.log)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())


__all__ = [
    "RiskGateAuditReport",
    "build_risk_gate_audit_report",
    "record_risk_gate_eval",
]
