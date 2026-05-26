"""Tests for P0 (data-freshness) + P1 (actionable + priority_rejected) health-check.

Scope:
- Stale-data flag suppresses base alert/cycle volume warnings (P0).
- Missing artifact files raise critical freshness issues (P0).
- Actionable-alert floor warning, with RE_ENTRY_MODE relax (P1).
- priority_rejected ratio warning, with RE_ENTRY_MODE relax (P1).
- Status-breakdown surfaces in HealthReport (P1).
- Backwards-compat: legacy `run_health_check()` still returns issues list.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.alerts.audit import (
    ALERT_AUDIT_JSONL_FILENAME,
    AlertAuditRecord,
    append_alert_audit,
)
from app.alerts.health_check import (
    HealthReport,
    run_health_check,
    run_health_check_report,
)


def _write_audit(tmp_path: Path, **kwargs) -> None:
    defaults = {
        "document_id": "doc-1",
        "channel": "telegram",
        "message_id": "test",
        "is_digest": False,
        "dispatched_at": datetime.now(UTC).isoformat(),
        "sentiment_label": "bullish",
        "affected_assets": ["BTC/USDT"],
        "directional_eligible": True,
    }
    defaults.update(kwargs)
    rec = AlertAuditRecord(**defaults)
    append_alert_audit(rec, tmp_path / ALERT_AUDIT_JSONL_FILENAME)


def _write_cycle(tmp_path: Path, status: str = "completed", **kwargs) -> None:
    defaults = {
        "cycle_id": f"c-{status}-{time.monotonic_ns()}",
        "started_at": datetime.now(UTC).isoformat(),
        "symbol": "BTC/USDT",
        "status": status,
    }
    defaults.update(kwargs)
    with (tmp_path / "trading_loop_audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(defaults) + "\n")


def _make_files_fresh(tmp_path: Path) -> None:
    """Ensure both audit files exist so freshness check passes."""
    (tmp_path / ALERT_AUDIT_JSONL_FILENAME).touch()
    (tmp_path / "trading_loop_audit.jsonl").touch()


# KAI_PI_HOSTNAME_MARKER autouse-fixture lives in tests/conftest.py.
# Off-Pi-specific tests below opt out via monkeypatch.setenv to a
# non-matching marker.


def test_missing_files_yield_critical_freshness_issues(tmp_path: Path) -> None:
    """Empty tmp_path = no files = 2 critical freshness issues + stale flag."""
    report = run_health_check_report(tmp_path)
    freshness_critical = [
        i for i in report.issues if i.component.endswith("_freshness") and i.severity == "critical"
    ]
    assert len(freshness_critical) == 2
    assert report.data_sources_stale is True


def test_stale_mtime_yields_freshness_warning(tmp_path: Path) -> None:
    """Files exist but mtime past per-file threshold -> warning + stale flag.

    alert_audit threshold is 480min (event-driven), trading_loop is 30min.
    10h-old mtime trips both.
    """
    _make_files_fresh(tmp_path)
    old_ts = (datetime.now(UTC) - timedelta(hours=10)).timestamp()
    os.utime(tmp_path / ALERT_AUDIT_JSONL_FILENAME, (old_ts, old_ts))
    os.utime(tmp_path / "trading_loop_audit.jsonl", (old_ts, old_ts))

    report = run_health_check_report(tmp_path)
    freshness_warnings = [
        i for i in report.issues if i.component.endswith("_freshness") and i.severity == "warning"
    ]
    assert len(freshness_warnings) == 2
    assert report.data_sources_stale is True


def test_recalc_outputs_missing_silent_when_absent(tmp_path: Path) -> None:
    """Recalc-cycle outputs are flagged required=False — fresh checkout with no
    recalc-run yet must NOT produce a critical issue.

    Backstop for kai-recalc-cycle.timer (PR #62, daily 04:00). Absent files on a
    new clone are normal; the staleness probe only fires once the file exists
    AND its mtime crosses the 1500min threshold.
    """
    _make_files_fresh(tmp_path)
    report = run_health_check_report(tmp_path)
    # No critical freshness issues should fire when only the recalc outputs are
    # missing — the alerts + trading_loop fixtures are fresh.
    recalc_components = {
        "bayes_recalc_freshness",
        "confluence_recalc_freshness",
        "ph5_recalc_freshness",
        "source_reliability_recalc_freshness",
    }
    recalc_critical = [
        i for i in report.issues if i.component in recalc_components and i.severity == "critical"
    ]
    assert recalc_critical == []


def test_stale_recalc_output_yields_freshness_warning(tmp_path: Path) -> None:
    """Stale bayes_posterior_state.json (>1500min) → bayes_recalc_freshness warn.

    Triggers when kai-recalc-cycle.timer deactivated silently — the exact
    failure mode that produced the 2026-05-16..24 8-day stall on Pi.
    """
    _make_files_fresh(tmp_path)
    bayes_path = tmp_path / "bayes_posterior_state.json"
    bayes_path.write_text("{}", encoding="utf-8")
    # 2 days old = past 25h threshold
    two_days = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(bayes_path, (two_days, two_days))

    report = run_health_check_report(tmp_path)
    bayes_warn = [i for i in report.issues if i.component == "bayes_recalc_freshness"]
    assert len(bayes_warn) == 1
    assert bayes_warn[0].severity == "warning"
    assert report.data_sources_stale is True


def test_alert_audit_quiet_channel_does_not_trip_freshness(tmp_path: Path) -> None:
    """alert_audit 2h gap = legit-quiet channel; should NOT flag stale.

    Lehre 1 aus 2026-05-23 Pi-Sprint: alert_audit ist eventbasiert. Pi-Run
    12:15 CEST hatte 122min alten alert_audit (threshold 120min war zu eng) +
    feuerte stale + suppresste den echten priority_rejected-Warning.
    """
    _make_files_fresh(tmp_path)
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    # alert_audit alone 2h old: should still be fresh (threshold 480min)
    os.utime(tmp_path / ALERT_AUDIT_JSONL_FILENAME, (two_hours_ago, two_hours_ago))
    # trading_loop fresh
    report = run_health_check_report(tmp_path)
    alert_freshness = [i for i in report.issues if i.component == "alerts_freshness"]
    assert alert_freshness == []
    assert report.data_sources_stale is False


def test_stale_suppresses_volume_warnings(tmp_path: Path) -> None:
    """When data is stale, base alerts/cycles volume warnings must NOT fire."""
    _make_files_fresh(tmp_path)
    old_ts = (datetime.now(UTC) - timedelta(hours=10)).timestamp()
    os.utime(tmp_path / ALERT_AUDIT_JSONL_FILENAME, (old_ts, old_ts))
    os.utime(tmp_path / "trading_loop_audit.jsonl", (old_ts, old_ts))

    report = run_health_check_report(
        tmp_path,
        min_expected_alerts=100,
        min_expected_cycles=100,
    )
    base_alerts = [i for i in report.issues if i.component == "alerts"]
    base_cycles = [i for i in report.issues if i.component == "trading_loop"]
    assert base_alerts == []
    assert base_cycles == []


def test_fresh_files_no_freshness_warnings(tmp_path: Path) -> None:
    _make_files_fresh(tmp_path)
    report = run_health_check_report(tmp_path)
    freshness = [i for i in report.issues if i.component.endswith("_freshness")]
    assert freshness == []
    assert report.data_sources_stale is False


def test_actionable_count_surfaces_in_report(tmp_path: Path) -> None:
    _make_files_fresh(tmp_path)
    _write_audit(tmp_path, document_id="d1", actionable=True)
    _write_audit(tmp_path, document_id="d2", actionable=False)
    _write_audit(tmp_path, document_id="d3", actionable=True)
    _write_audit(tmp_path, document_id="d4")

    report = run_health_check_report(tmp_path)
    assert report.recent_alerts == 4
    assert report.recent_actionable_alerts == 2


def test_actionable_floor_warning_when_below_threshold(tmp_path: Path) -> None:
    _make_files_fresh(tmp_path)
    _write_audit(tmp_path, document_id="d1", actionable=False)
    _write_audit(tmp_path, document_id="d2", actionable=False)

    report = run_health_check_report(tmp_path, min_expected_actionable=1)
    actionable_warnings = [i for i in report.issues if i.component == "alerts_actionable"]
    assert len(actionable_warnings) == 1


def test_actionable_floor_disabled_when_threshold_zero(tmp_path: Path) -> None:
    _make_files_fresh(tmp_path)
    _write_audit(tmp_path, document_id="d1", actionable=False)

    report = run_health_check_report(tmp_path, min_expected_actionable=0)
    actionable_warnings = [i for i in report.issues if i.component == "alerts_actionable"]
    assert actionable_warnings == []


def test_actionable_floor_relaxed_during_re_entry_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE_ENTRY_MODE", "active")
    _make_files_fresh(tmp_path)
    _write_audit(tmp_path, document_id="d1", actionable=False)

    report = run_health_check_report(tmp_path, min_expected_actionable=5)
    actionable_warnings = [i for i in report.issues if i.component == "alerts_actionable"]
    assert actionable_warnings == []
    assert report.re_entry_mode_active is True


def test_re_entry_mode_enabled_alias_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pi `.env` uses RE_ENTRY_MODE_ENABLED=true; probe must honor that alias."""
    monkeypatch.delenv("RE_ENTRY_MODE", raising=False)
    monkeypatch.setenv("RE_ENTRY_MODE_ENABLED", "true")
    _make_files_fresh(tmp_path)
    for _ in range(20):
        _write_cycle(tmp_path, status="priority_rejected")

    report = run_health_check_report(tmp_path)
    assert report.re_entry_mode_active is True
    sig_health = [i for i in report.issues if i.component == "trading_loop_signal_health"]
    assert sig_health == []


def test_priority_rejected_ratio_surfaces_in_breakdown(tmp_path: Path) -> None:
    _make_files_fresh(tmp_path)
    for _ in range(8):
        _write_cycle(tmp_path, status="priority_rejected")
    _write_cycle(tmp_path, status="completed")
    _write_cycle(tmp_path, status="completed")

    report = run_health_check_report(tmp_path)
    assert report.cycle_status_breakdown.get("priority_rejected") == 8
    assert report.cycle_status_breakdown.get("completed") == 2


def test_priority_rejected_warning_at_100pct(tmp_path: Path) -> None:
    _make_files_fresh(tmp_path)
    for _ in range(20):
        _write_cycle(tmp_path, status="priority_rejected")

    report = run_health_check_report(tmp_path)
    sig_health = [i for i in report.issues if i.component == "trading_loop_signal_health"]
    assert len(sig_health) == 1
    assert "100%" in sig_health[0].message


def test_priority_rejected_no_warning_below_threshold(tmp_path: Path) -> None:
    """At 94% rejected, no warning (default threshold = >0.95)."""
    _make_files_fresh(tmp_path)
    for _ in range(94):
        _write_cycle(tmp_path, status="priority_rejected")
    for _ in range(6):
        _write_cycle(tmp_path, status="completed")

    report = run_health_check_report(tmp_path)
    sig_health = [i for i in report.issues if i.component == "trading_loop_signal_health"]
    assert sig_health == []


def test_priority_rejected_relaxed_during_re_entry_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE_ENTRY_MODE", "active")
    _make_files_fresh(tmp_path)
    for _ in range(20):
        _write_cycle(tmp_path, status="priority_rejected")

    report = run_health_check_report(tmp_path)
    sig_health = [i for i in report.issues if i.component == "trading_loop_signal_health"]
    assert sig_health == []


def test_legacy_run_health_check_returns_list(tmp_path: Path) -> None:
    _make_files_fresh(tmp_path)
    _write_audit(tmp_path)
    _write_cycle(tmp_path)

    result = run_health_check(tmp_path)
    assert isinstance(result, list)
    assert all(i.severity in ("warning", "critical") for i in result)


def test_report_dataclass_exposes_state(tmp_path: Path) -> None:
    _make_files_fresh(tmp_path)
    _write_audit(tmp_path, actionable=True)
    _write_cycle(tmp_path, status="completed")

    report = run_health_check_report(tmp_path)
    assert isinstance(report, HealthReport)
    assert report.recent_alerts == 1
    assert report.recent_actionable_alerts == 1
    assert report.recent_cycles == 1
    assert report.cycle_status_breakdown == {"completed": 1}
    assert report.data_sources_stale is False
    # P2: hostname + runs_on_pi always populated
    assert isinstance(report.hostname, str)
    assert isinstance(report.runs_on_pi, bool)


# ── P2: workstation-redirect ─────────────────────────────────────────


def test_p2_hostname_marker_detects_pi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KAI_PI_HOSTNAME_MARKER env override is recognized regardless of real host."""
    import socket as _socket

    real_host = _socket.gethostname()
    if not real_host:
        pytest.skip("host without gethostname result")
    monkeypatch.setenv("KAI_PI_HOSTNAME_MARKER", real_host.lower())
    _make_files_fresh(tmp_path)

    report = run_health_check_report(tmp_path)
    assert report.runs_on_pi is True


def test_p2_probe_location_issue_when_off_pi_and_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off-Pi + stale data → probe_location warning fires (drives exit-on-stale path)."""
    # Force non-Pi hostname
    monkeypatch.setenv("KAI_PI_HOSTNAME_MARKER", "__never_matches__")
    _make_files_fresh(tmp_path)
    old_ts = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(tmp_path / ALERT_AUDIT_JSONL_FILENAME, (old_ts, old_ts))
    os.utime(tmp_path / "trading_loop_audit.jsonl", (old_ts, old_ts))

    report = run_health_check_report(tmp_path)
    location_issues = [i for i in report.issues if i.component == "probe_location"]
    assert len(location_issues) == 1
    assert "stale" in location_issues[0].message.lower()
    assert report.runs_on_pi is False


def test_p2_no_probe_location_issue_when_runs_on_pi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the probe runs on Pi (or marker matches), no probe_location warning."""
    import socket as _socket

    real_host = _socket.gethostname()
    if not real_host:
        pytest.skip("host without gethostname result")
    monkeypatch.setenv("KAI_PI_HOSTNAME_MARKER", real_host.lower())
    _make_files_fresh(tmp_path)
    old_ts = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(tmp_path / ALERT_AUDIT_JSONL_FILENAME, (old_ts, old_ts))
    os.utime(tmp_path / "trading_loop_audit.jsonl", (old_ts, old_ts))

    report = run_health_check_report(tmp_path)
    location_issues = [i for i in report.issues if i.component == "probe_location"]
    # Still stale, but not flagged as wrong-location because we claim to be on Pi
    assert location_issues == []
    assert report.runs_on_pi is True
    assert report.data_sources_stale is True
