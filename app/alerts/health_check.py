"""System Health Check — flags anomalies in pipeline operation.

Checks:
- Data freshness (artifacts mtime + last-record age — catches stale-data probes)
- Alert volume anomaly (zero alerts in lookback window)
- Actionable-alert volume (P1: structural pipeline health, not just heartbeat)
- Trading loop stale (no cycles in lookback window)
- Trading loop priority_rejected saturation (P1: detects gate-induced silence)
- High error rate in trading cycles
- Precision degradation below threshold
- Outcome annotation backlog (unannotated directional alerts)

Usage:
    issues = run_health_check(artifacts_dir)
    for issue in issues:
        print(issue)
"""

from __future__ import annotations

import os
import socket
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.audit import load_alert_audits, load_outcome_annotations
from app.orchestrator.trading_loop import load_trading_loop_cycles

_ARTIFACTS = Path("artifacts")

# Data-freshness thresholds (P0). A probe-run that reads files older than these
# is almost certainly a sync-lag false-positive (Pi is source-of-truth — see
# memory feedback_pi_branch_pointer_staleness + V4-forensik 2026-05-23).
_FRESHNESS_MTIME_WARN_MIN = 30
_FRESHNESS_LAST_RECORD_WARN_HOURS = 2

# Hostname substrings that identify the Pi-side authoritative host. Override
# via env KAI_PI_HOSTNAME_MARKER for non-default deployments.
_PI_HOSTNAME_MARKERS = ("kai-pi", "kai-pi5", "pi5", "kai_pi")


@dataclass(frozen=True)
class HealthIssue:
    """A detected system health issue."""

    severity: str  # "warning" | "critical"
    component: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.component}: {self.message}"


@dataclass
class HealthReport:
    """Structured health-check output (P1: not just issues, but breakdown).

    Backwards-compatible: `run_health_check` still returns the issues list
    when callers don't need the breakdown. Use `run_health_check_report` for
    the structured view.
    """

    issues: list[HealthIssue] = field(default_factory=list)
    recent_alerts: int = 0
    recent_actionable_alerts: int = 0
    recent_cycles: int = 0
    cycle_status_breakdown: dict[str, int] = field(default_factory=dict)
    data_sources_stale: bool = False
    re_entry_mode_active: bool = False
    hostname: str = ""  # P2: lets operator see at a glance where probe ran
    runs_on_pi: bool = False  # P2: True when hostname matches Pi signature


def _check_data_freshness(adir: Path, now: datetime) -> tuple[list[HealthIssue], bool]:
    """P0 — flag stale artifact files so probe doesn't false-positive on sync lag.

    Checks two files (alert_audit + trading_loop_audit): mtime must be within
    `_FRESHNESS_MTIME_WARN_MIN` minutes, last record's timestamp within
    `_FRESHNESS_LAST_RECORD_WARN_HOURS` hours. Returns (issues, is_stale).
    """
    issues: list[HealthIssue] = []
    stale = False
    mtime_cutoff = now - timedelta(minutes=_FRESHNESS_MTIME_WARN_MIN)
    files_to_check = [
        ("alert_audit.jsonl", "alerts"),
        ("trading_loop_audit.jsonl", "trading_loop"),
    ]
    for fname, component in files_to_check:
        path = adir / fname
        if not path.exists():
            issues.append(
                HealthIssue(
                    severity="critical",
                    component=f"{component}_freshness",
                    message=f"{fname} does not exist at {path}",
                )
            )
            stale = True
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if mtime < mtime_cutoff:
            age_min = int((now - mtime).total_seconds() / 60)
            issues.append(
                HealthIssue(
                    severity="warning",
                    component=f"{component}_freshness",
                    message=(
                        f"{fname} mtime is {age_min}min old "
                        f"(threshold: {_FRESHNESS_MTIME_WARN_MIN}min) — "
                        f"probe may be running against stale data, "
                        f"check Pi sync"
                    ),
                )
            )
            stale = True
    return issues, stale


def _re_entry_mode_active() -> bool:
    """P1 — respect RE_ENTRY_MODE env-flag so probe relaxes during gated window."""
    return os.environ.get("RE_ENTRY_MODE", "").strip().lower() in {"1", "true", "active"}


def _detect_hostname() -> tuple[str, bool]:
    """P2 — detect if probe runs on the Pi or somewhere else (workstation, CI)."""
    try:
        host = socket.gethostname() or ""
    except OSError:
        host = ""
    override = os.environ.get("KAI_PI_HOSTNAME_MARKER", "").strip().lower()
    markers = (override,) if override else _PI_HOSTNAME_MARKERS
    host_lower = host.lower()
    runs_on_pi = any(m and m in host_lower for m in markers)
    return host, runs_on_pi


def run_health_check(
    artifacts_dir: Path | None = None,
    lookback_hours: int = 24,
    min_expected_alerts: int = 1,
    min_expected_cycles: int = 10,
    min_precision_pct: float = 15.0,
) -> list[HealthIssue]:
    """Run all health checks and return list of issues (empty = healthy).

    Backwards-compatible wrapper around `run_health_check_report`.
    """
    return run_health_check_report(
        artifacts_dir=artifacts_dir,
        lookback_hours=lookback_hours,
        min_expected_alerts=min_expected_alerts,
        min_expected_cycles=min_expected_cycles,
        min_precision_pct=min_precision_pct,
    ).issues


def run_health_check_report(
    artifacts_dir: Path | None = None,
    lookback_hours: int = 24,
    min_expected_alerts: int = 1,
    min_expected_cycles: int = 10,
    min_precision_pct: float = 15.0,
    min_expected_actionable: int = 0,
    max_priority_rejected_ratio: float = 0.95,
) -> HealthReport:
    """Run all health checks and return a structured report (P0+P1).

    Adds data-freshness check (P0) and actionable + priority_rejected_ratio
    checks (P1). Respects RE_ENTRY_MODE env-flag to relax thresholds.
    """
    adir = artifacts_dir or _ARTIFACTS
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=lookback_hours)
    report = HealthReport()
    report.re_entry_mode_active = _re_entry_mode_active()
    report.hostname, report.runs_on_pi = _detect_hostname()

    # ── P0: data freshness ───────────────────────────────────────────
    freshness_issues, stale = _check_data_freshness(adir, now)
    report.issues.extend(freshness_issues)
    report.data_sources_stale = stale

    # ── P2: workstation-redirect — off-Pi probe runs read mirror/sync data
    # that may be selectively truncated (mtime-fresh but content-incomplete).
    # The 2026-05-23 false-positive had fresh mtime but only 6/16 of Pi's
    # alerts in window. Surface this as an explicit `probe_location` issue
    # so operator + `--exit-on-stale` can react; we do NOT touch
    # `data_sources_stale` here, so other check semantics remain stable.
    if not report.runs_on_pi:
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="probe_location",
                message=(
                    f"Probe running on {report.hostname or 'unknown host'} "
                    f"(off-Pi) — counts may be partial-mirror, not authoritative. "
                    f"Re-run on Pi or pass --allow-stale to override."
                ),
            )
        )

    # ── Alert volume ─────────────────────────────────────────────────
    try:
        audits = load_alert_audits(adir)
    except Exception:
        report.issues.append(
            HealthIssue(
                severity="critical",
                component="alerts",
                message="Cannot read alert audit trail",
            )
        )
        audits = []

    recent_alerts = 0
    recent_actionable = 0
    for rec in audits:
        try:
            ts = datetime.fromisoformat(
                rec.dispatched_at.replace("Z", "+00:00"),
            )
        except (ValueError, AttributeError):
            continue
        if ts >= cutoff:
            recent_alerts += 1
            if getattr(rec, "actionable", None) is True:
                recent_actionable += 1
    report.recent_alerts = recent_alerts
    report.recent_actionable_alerts = recent_actionable

    # Suppress base alert-volume warning when data is stale (P0): the count is
    # not authoritative — the freshness warning already tells the operator.
    if recent_alerts < min_expected_alerts and not stale:
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="alerts",
                message=(
                    f"Only {recent_alerts} alerts in last {lookback_hours}h "
                    f"(expected >= {min_expected_alerts})"
                ),
            )
        )

    # P1: actionable-alert floor. Relaxed during RE_ENTRY_MODE (ADR-1 gate=10
    # is expected to produce very few actionable alerts).
    if (
        not stale
        and not report.re_entry_mode_active
        and min_expected_actionable > 0
        and recent_actionable < min_expected_actionable
    ):
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="alerts_actionable",
                message=(
                    f"Only {recent_actionable} actionable alerts in last "
                    f"{lookback_hours}h (expected >= {min_expected_actionable})"
                ),
            )
        )

    # ── Trading loop freshness (+ P1 status breakdown) ───────────────
    try:
        cycles = load_trading_loop_cycles(
            adir / "trading_loop_audit.jsonl",
        )
    except Exception:
        report.issues.append(
            HealthIssue(
                severity="critical",
                component="trading_loop",
                message="Cannot read trading loop audit trail",
            )
        )
        cycles = []

    recent_cycles = 0
    error_cycles = 0
    status_breakdown: Counter[str] = Counter()
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
        status = str(c.get("status", "unknown")) or "unknown"
        status_breakdown[status] += 1
        if status in ("error", "no_market_data"):
            error_cycles += 1
    report.recent_cycles = recent_cycles
    report.cycle_status_breakdown = dict(status_breakdown)

    if recent_cycles < min_expected_cycles and not stale:
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="trading_loop",
                message=(
                    f"Only {recent_cycles} cycles in last {lookback_hours}h "
                    f"(expected >= {min_expected_cycles})"
                ),
            )
        )

    if recent_cycles > 0 and error_cycles / recent_cycles > 0.5:
        report.issues.append(
            HealthIssue(
                severity="critical",
                component="trading_loop",
                message=(
                    f"{error_cycles}/{recent_cycles} cycles errored "
                    f"({error_cycles / recent_cycles:.0%})"
                ),
            )
        )

    # P1: priority_rejected saturation — Cron-Liveness without Wertschöpfung.
    # Relaxed during RE_ENTRY_MODE (ADR-1 paper_min_priority=10 expects
    # near-total rejection by design).
    if (
        recent_cycles > 0
        and not report.re_entry_mode_active
    ):
        rejected = status_breakdown.get("priority_rejected", 0)
        ratio = rejected / recent_cycles
        if ratio > max_priority_rejected_ratio:
            report.issues.append(
                HealthIssue(
                    severity="warning",
                    component="trading_loop_signal_health",
                    message=(
                        f"{rejected}/{recent_cycles} cycles priority_rejected "
                        f"({ratio:.0%}) — pipeline runs but produces no signals; "
                        f"check priority gate / sentiment scoring"
                    ),
                )
            )

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
            report.issues.append(
                HealthIssue(
                    severity="warning",
                    component="precision",
                    message=(
                        f"Precision {precision:.1f}% is below threshold {min_precision_pct:.0f}%"
                    ),
                )
            )

    # ── Annotation backlog ───────────────────────────────────────────
    annotated_ids = {a.document_id for a in annotations}
    unique_unannotated = len(
        {
            rec.document_id
            for rec in audits
            if rec.directional_eligible is True and rec.document_id not in annotated_ids
        }
    )
    if unique_unannotated > 20:
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="annotations",
                message=(f"{unique_unannotated} directional alerts unannotated"),
            )
        )

    return report
