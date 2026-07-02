"""System Health Check — flags anomalies in pipeline operation.

Checks:
- Data freshness (artifacts mtime + last-record age — catches stale-data probes)
- Alert volume anomaly (zero alerts in lookback window)
- Actionable-alert volume (P1: structural pipeline health, not just heartbeat)
- Trading loop stale (no cycles in lookback window)
- Trading loop priority_rejected saturation (P1: detects gate-induced silence)
- Trading loop open-deadlock (V5: loop spins but opens no positions)
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
from app.audit.stream_validation import AuditStreamName, load_audit_stream
from app.orchestrator.trading_loop import load_trading_loop_cycles

_ARTIFACTS = Path("artifacts")

# Data-freshness thresholds (P0). A probe-run that reads files older than these
# is almost certainly a sync-lag false-positive (Pi is source-of-truth — see
# memory feedback_pi_branch_pointer_staleness + V4-forensik 2026-05-23).
#
# Per-file thresholds (see feedback_health_probe_design_lessons.md Lehre 1):
# - alert_audit.jsonl is event-driven: it only writes when the Telegram channel
#   dispatches. Quiet hours / weekends commonly produce 4-8h gaps in low-vol
#   phases. Use a wide window (8h) so legitimate quiet is not flagged.
# - trading_loop_audit.jsonl is timer-driven (~5min cycles). A multi-hour gap
#   indicates a real broken scheduler. Use a tight window.
_FRESHNESS_DEFAULT_MIN = 120
_FRESHNESS_PER_FILE_MIN: dict[str, int] = {
    "alert_audit.jsonl": 480,  # 8h — event-driven channel
    "trading_loop_audit.jsonl": 30,  # 5min cycle → 30min is 6 missed runs
    # Recalc-cycle outputs (kai-recalc-cycle.timer, daily 04:00 Pi-local).
    # Threshold 1500min = 25h covers next-day-run + RandomizedDelaySec=120s
    # + longest recalc runtime (~5min ph5_feature) + 1h grace.
    "bayes_posterior_state.json": 1500,
    "source_confluence_audit.jsonl": 1500,
    "ph5_feature_analysis.json": 1500,
    "source_reliability.json": 1500,  # lives in monitor/, not artifacts/
}
_FRESHNESS_LAST_RECORD_WARN_HOURS = 4

# V5 loop-deadlock watchdog (DS-20260531-V5). The 2026-05-31 incident: the loop
# ran ~24h of cycles (trading_loop_audit fresh, so the freshness + min-cycles
# checks stayed green) while EVERY cycle was rejected at the diversification /
# sizing gate — zero orders opened, paper_execution_audit frozen for ~24h. No
# existing check fired: the priority_rejected-saturation check only looks at
# `priority_rejected` AND is disabled under RE_ENTRY_MODE. This watchdog catches
# the general "loop spins but opens nothing" failure, RE_ENTRY_MODE-independent.
#
# Discriminator against a legitimately FULL book (also 0 completed): a full book
# rejects new entries with `risk_rejected` (max_open_positions), NOT
# diversification/size. So we only fire when the OPEN-blocking gates dominate.
_OPEN_BLOCKING_STATUSES: frozenset[str] = frozenset(
    {"diversification_rejected", "size_rejected", "sizing_anomaly_rejected"}
)
# paper_execution_audit is event-driven (writes only on a fill/close), so it is
# deliberately NOT in the timer-driven freshness list. Its staleness is only
# meaningful as a SECONDARY signal alongside an active-but-unproductive loop.
_PAPER_EXECUTION_SILENCE_MIN = 180  # 3h — informative threshold for the message

# Hostname substrings that identify the Pi-side authoritative host. Override
# via env KAI_PI_HOSTNAME_MARKER for non-default deployments.
_PI_HOSTNAME_MARKERS = ("kai-pi", "kai-pi5", "pi5", "kai_pi")
_AUDIT_STREAM_SCHEMA_FILES: tuple[tuple[AuditStreamName, str], ...] = (
    ("alert_audit", "alert_audit.jsonl"),
    ("blocked_alerts", "blocked_alerts.jsonl"),
    ("paper_execution_audit", "paper_execution_audit.jsonl"),
    ("decision_journal", "decision_journal.jsonl"),
    ("bayes_confidence_audit", "bayes_confidence_audit.jsonl"),
)


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

    Per-file mtime thresholds (see ``_FRESHNESS_PER_FILE_MIN``): event-driven
    streams (alert_audit) get a wide window; timer-driven streams
    (trading_loop_audit) get a tight one. Returns (issues, is_stale).
    """
    issues: list[HealthIssue] = []
    stale = False
    # (path, fname, component, required). source_reliability.json sits in
    # monitor/, the others in artifacts/. Recalc-cycle outputs are flagged
    # required=False so a fresh-checkout (no recalc-run yet) does not trip the
    # probe; once they exist they are subject to the 1500min staleness
    # threshold, which catches a silent kai-recalc-cycle.timer (e.g. the
    # 2026-05-16..24 8-day stall that motivated this patch).
    monitor_dir = adir.parent / "monitor"
    files_to_check = [
        (adir / "alert_audit.jsonl", "alert_audit.jsonl", "alerts", True),
        (adir / "trading_loop_audit.jsonl", "trading_loop_audit.jsonl", "trading_loop", True),
        (adir / "bayes_posterior_state.json", "bayes_posterior_state.json", "bayes_recalc", False),
        (
            adir / "source_confluence_audit.jsonl",
            "source_confluence_audit.jsonl",
            "confluence_recalc",
            False,
        ),
        (adir / "ph5_feature_analysis.json", "ph5_feature_analysis.json", "ph5_recalc", False),
        (
            monitor_dir / "source_reliability.json",
            "source_reliability.json",
            "source_reliability_recalc",
            False,
        ),
    ]
    for path, fname, component, required in files_to_check:
        if not path.exists():
            if not required:
                continue
            issues.append(
                HealthIssue(
                    severity="critical",
                    component=f"{component}_freshness",
                    message=f"{fname} does not exist at {path}",
                )
            )
            stale = True
            continue
        threshold_min = _FRESHNESS_PER_FILE_MIN.get(fname, _FRESHNESS_DEFAULT_MIN)
        mtime_cutoff = now - timedelta(minutes=threshold_min)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if mtime < mtime_cutoff:
            age_min = int((now - mtime).total_seconds() / 60)
            issues.append(
                HealthIssue(
                    severity="warning",
                    component=f"{component}_freshness",
                    message=(
                        f"{fname} mtime is {age_min}min old "
                        f"(threshold: {threshold_min}min) — "
                        f"probe may be running against stale data, "
                        f"check Pi sync"
                    ),
                )
            )
            stale = True
    return issues, stale


def _check_audit_stream_schemas(adir: Path) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    for stream, filename in _AUDIT_STREAM_SCHEMA_FILES:
        result = load_audit_stream(adir / filename, stream)
        if not result.issues:
            continue
        first = result.issues[0]
        issues.append(
            HealthIssue(
                severity="warning",
                component=f"{stream}_schema",
                message=(
                    f"{result.issue_count} invalid row(s) in {filename}; "
                    f"first at line {first.line_number}: {first.message.splitlines()[0]}"
                ),
            )
        )
    return issues


def _paper_execution_silence_hint(adir: Path, now: datetime) -> str:
    """Append-able hint about paper_execution_audit staleness (V5 secondary signal).

    Returns ``""`` when the file is missing or fresh, otherwise a short
    `; paper_execution_audit silent for Nh` suffix. Purely informative — the
    deadlock trigger itself is the completed==0 + open-blocking-ratio condition.
    """
    path = adir / "paper_execution_audit.jsonl"
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except (OSError, ValueError):
        return ""
    age_min = (now - mtime).total_seconds() / 60
    if age_min < _PAPER_EXECUTION_SILENCE_MIN:
        return ""
    return f"; paper_execution_audit silent for {age_min / 60:.1f}h"


def _re_entry_mode_active() -> bool:
    """P1 — respect RE_ENTRY_MODE env-flag so probe relaxes during gated window.

    Accepts two key variants because the codebase has both in circulation:
    - ``RE_ENTRY_MODE`` (legacy, accepts "active"/"true"/"1")
    - ``RE_ENTRY_MODE_ENABLED`` (current Pi `.env` form, boolean-like)
    """
    truthy = {"1", "true", "active", "yes", "on"}
    for key in ("RE_ENTRY_MODE", "RE_ENTRY_MODE_ENABLED"):
        if os.environ.get(key, "").strip().lower() in truthy:
            return True
    return False


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
    max_open_blocking_ratio: float = 0.5,
) -> HealthReport:
    """Run all health checks and return a structured report (P0+P1+V5).

    Adds data-freshness check (P0), actionable + priority_rejected_ratio
    checks (P1), and the loop open-deadlock watchdog (V5). Respects
    RE_ENTRY_MODE env-flag to relax thresholds — except V5, which fires
    regardless because a self-inflicted open-deadlock is never intended.
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
    report.issues.extend(_check_audit_stream_schemas(adir))

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
    if recent_cycles > 0 and not report.re_entry_mode_active:
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

    # ── V5: loop open-deadlock watchdog (DS-20260531-V5) ─────────────
    # "Loop spins but opens nothing." Fires when the loop is demonstrably
    # active (>= min_expected_cycles) yet produced ZERO completed cycles AND
    # the open-blocking gates (diversification / sizing) dominate. This is the
    # exact 2026-05-31 deadlock signature; it is intentionally
    # RE_ENTRY_MODE-INDEPENDENT because a self-inflicted open-deadlock is never
    # a designed state (unlike priority_rejected saturation, which RE_ENTRY_MODE
    # expects). A legitimately full book is excluded: it rejects with
    # `risk_rejected` (max_open_positions), so the open-blocking ratio stays low.
    if recent_cycles > 0 and recent_cycles >= min_expected_cycles and not stale:
        completed = status_breakdown.get("completed", 0)
        open_blocked = sum(status_breakdown.get(s, 0) for s in _OPEN_BLOCKING_STATUSES)
        open_blocked_ratio = open_blocked / recent_cycles
        if completed == 0 and open_blocked_ratio >= max_open_blocking_ratio:
            dominant = max(
                _OPEN_BLOCKING_STATUSES,
                key=lambda s: status_breakdown.get(s, 0),
            )
            paper_hint = _paper_execution_silence_hint(adir, now)
            report.issues.append(
                HealthIssue(
                    severity="critical",
                    component="trading_loop_open_deadlock",
                    message=(
                        f"{open_blocked}/{recent_cycles} cycles "
                        f"{dominant} ({open_blocked_ratio:.0%}), 0 completed "
                        f"— loop spins but opens no positions (self-deadlock at "
                        f"the {dominant.replace('_rejected', '')} gate)"
                        f"{paper_hint}"
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
