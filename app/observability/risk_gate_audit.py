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
    total_records: int  # raw audit lines (one per bridge-tick eval)
    would_reject_count: int  # DISTINCT flagged signals (deduped by correlation_id)
    reject_rate: float
    reason_code_distribution: dict[str, int]
    rejected_by_symbol: dict[str, int]
    rejected_by_source: dict[str, int]
    enforced_count: int  # distinct signals seen under enforce mode
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
    """Aggregate the risk-gate audit JSONL — DEDUPED per distinct signal.

    The bridge writes one audit line per pending re-evaluation (every tick), so
    a single bad pending signal produces hundreds of identical flags. Counting
    raw lines yields a nonsensical "rate" > 1. We therefore dedup by a stable
    signal key (``correlation_id`` -> ``envelope_id`` -> line index): each
    distinct flagged SIGNAL counts once, its codes unioned across its evals.
    ``total_records`` keeps the raw line count for transparency.
    """
    path = Path(log_path) if log_path else _DEFAULT_LOG
    notes: list[str] = []

    if not path.exists():
        return RiskGateAuditReport(0, 0, 0.0, {}, {}, {}, 0, ["no audit file yet"])

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return RiskGateAuditReport(0, 0, 0.0, {}, {}, {}, 0, [f"read failed: {exc}"])

    total = 0
    # Per-distinct-signal accumulation.
    sig_codes: dict[str, set[str]] = {}
    sig_symbol: dict[str, str] = {}
    sig_source: dict[str, str] = {}
    sig_enforced: dict[str, bool] = {}

    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1
        if not rec.get("would_reject"):
            continue
        key = rec.get("correlation_id") or rec.get("envelope_id") or f"_line{idx}"
        key = str(key)
        sig_codes.setdefault(key, set()).update(
            str(c) for c in (rec.get("would_reject_codes") or [])
        )
        sym = rec.get("symbol")
        if isinstance(sym, str) and key not in sig_symbol:
            sig_symbol[key] = sym
        src = rec.get("source")
        if isinstance(src, str) and key not in sig_source:
            sig_source[key] = src
        sig_enforced[key] = sig_enforced.get(key, False) or bool(rec.get("enforced"))

    distinct_flagged = len(sig_codes)
    code_dist: Counter[str] = Counter()
    for codes in sig_codes.values():
        for c in codes:
            code_dist[c] += 1
    by_symbol: Counter[str] = Counter(sig_symbol.values())
    by_source: Counter[str] = Counter(sig_source.values())
    enforced = sum(1 for v in sig_enforced.values() if v)

    notes.append(
        "would_reject_count = DISTINCT flagged signals (deduped by correlation_id); "
        f"total_records={total} raw per-tick eval lines. reject_rate is computed "
        "downstream against the bridge denominator (distinct evaluated signals). "
        "False-positive ID needs outcome correlation (alert_outcomes) + human review."
    )
    return RiskGateAuditReport(
        total_records=total,
        would_reject_count=distinct_flagged,
        # population rate is computed in the review layer (needs the bridge
        # denominator); here we expose distinct-flagged, not a self-referential rate.
        reject_rate=0.0,
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
