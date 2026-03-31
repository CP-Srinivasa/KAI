"""System Health Check — flags anomalies in pipeline operation.

Checks:
- Alert volume anomaly (zero alerts in lookback window)
- Trading loop stale (no cycles in lookback window)
- High error rate in trading cycles
- Precision degradation below threshold
- Outcome annotation backlog (unannotated directional alerts)

Usage:
    issues = run_health_check(artifacts_dir)
    for issue in issues:
        print(issue)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.audit import load_alert_audits, load_outcome_annotations
from app.orchestrator.trading_loop import load_trading_loop_cycles

_ARTIFACTS = Path("artifacts")


@dataclass(frozen=True)
class HealthIssue:
    """A detected system health issue."""

    severity: str  # "warning" | "critical"
    component: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.component}: {self.message}"


def run_health_check(
    artifacts_dir: Path | None = None,
    lookback_hours: int = 24,
    min_expected_alerts: int = 1,
    min_expected_cycles: int = 10,
    min_precision_pct: float = 15.0,
) -> list[HealthIssue]:
    """Run all health checks and return list of issues (empty = healthy)."""
    adir = artifacts_dir or _ARTIFACTS
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=lookback_hours)
    issues: list[HealthIssue] = []

    # ── Alert volume ─────────────────────────────────────────────────
    try:
        audits = load_alert_audits(adir)
    except Exception:
        issues.append(HealthIssue(
            severity="critical",
            component="alerts",
            message="Cannot read alert audit trail",
        ))
        audits = []

    recent_alerts = 0
    for rec in audits:
        try:
            ts = datetime.fromisoformat(
                rec.dispatched_at.replace("Z", "+00:00"),
            )
        except (ValueError, AttributeError):
            continue
        if ts >= cutoff:
            recent_alerts += 1

    if recent_alerts < min_expected_alerts:
        issues.append(HealthIssue(
            severity="warning",
            component="alerts",
            message=(
                f"Only {recent_alerts} alerts in last {lookback_hours}h "
                f"(expected >= {min_expected_alerts})"
            ),
        ))

    # ── Trading loop freshness ───────────────────────────────────────
    try:
        cycles = load_trading_loop_cycles(
            adir / "trading_loop_audit.jsonl",
        )
    except Exception:
        issues.append(HealthIssue(
            severity="critical",
            component="trading_loop",
            message="Cannot read trading loop audit trail",
        ))
        cycles = []

    recent_cycles = 0
    error_cycles = 0
    for c in cycles:
        ts_str = c.get("started_at", "")
        try:
            ts = datetime.fromisoformat(
                str(ts_str).replace("Z", "+00:00"),
            )
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        recent_cycles += 1
        if str(c.get("status", "")) in ("error", "no_market_data"):
            error_cycles += 1

    if recent_cycles < min_expected_cycles:
        issues.append(HealthIssue(
            severity="warning",
            component="trading_loop",
            message=(
                f"Only {recent_cycles} cycles in last {lookback_hours}h "
                f"(expected >= {min_expected_cycles})"
            ),
        ))

    if recent_cycles > 0 and error_cycles / recent_cycles > 0.5:
        issues.append(HealthIssue(
            severity="critical",
            component="trading_loop",
            message=(
                f"{error_cycles}/{recent_cycles} cycles errored "
                f"({error_cycles/recent_cycles:.0%})"
            ),
        ))

    # ── Precision ────────────────────────────────────────────────────
    try:
        annotations = load_outcome_annotations(adir)
    except Exception:
        annotations = []

    hits = sum(1 for a in annotations if a.outcome == "hit")
    misses = sum(1 for a in annotations if a.outcome == "miss")
    resolved = hits + misses
    if resolved >= 20:
        precision = hits / resolved * 100
        if precision < min_precision_pct:
            issues.append(HealthIssue(
                severity="warning",
                component="precision",
                message=(
                    f"Precision {precision:.1f}% is below "
                    f"threshold {min_precision_pct:.0f}%"
                ),
            ))

    # ── Annotation backlog ───────────────────────────────────────────
    annotated_ids = {a.document_id for a in annotations}
    unique_unannotated = len({
        rec.document_id for rec in audits
        if rec.directional_eligible is True
        and rec.document_id not in annotated_ids
    })
    if unique_unannotated > 20:
        issues.append(HealthIssue(
            severity="warning",
            component="annotations",
            message=(
                f"{unique_unannotated} directional alerts unannotated"
            ),
        ))

    return issues
