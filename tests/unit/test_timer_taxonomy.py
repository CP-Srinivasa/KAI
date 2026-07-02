"""FS-2 (#198) — timer taxonomy + severity + alert policy.

A one-shot timer pinned to a fixed past date (e.g. kai-risk-gate-audit-review)
is EXPECTED inactive after it fired and must NOT raise an alarm. A recurring
timer being inactive — or any systemd-failed unit — is critical. Active alerts
only fire for critical recurring/failed timers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.services.timer_health import (
    classify_timer_schedule,
    read_latest_timer_audit,
    timer_category,
    timers_warranting_alert,
)


def _write_audit(tmp_path: Path, findings: list[str]) -> Path:
    f = tmp_path / "timer_health.jsonl"
    rec = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "event": "timer_health_probe.findings",
        "findings": findings,
        "total_timers": 12,
        "active_timers": 12 - len(findings),
    }
    f.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return f


# ── classify_timer_schedule (pure) ──────────────────────────────────────────


def test_classify_wildcard_oncalendar_is_recurring() -> None:
    assert classify_timer_schedule("*-*-* *:05:00") == "recurring_required"


def test_classify_fixed_date_oncalendar_is_one_shot() -> None:
    assert classify_timer_schedule("2026-06-04 16:00:00 UTC") == "one_shot_expected_inactive"


def test_classify_onboot_relative_is_recurring() -> None:
    assert classify_timer_schedule(None, "45min", None) == "recurring_required"


def test_classify_no_trigger_is_disabled_by_design() -> None:
    assert classify_timer_schedule(None, None, None) == "disabled_by_design"


# ── timer_category against the real deploy/systemd catalog ───────────────────


def test_category_known_one_shot_units() -> None:
    assert timer_category("kai-risk-gate-audit-review") == "one_shot_expected_inactive"
    assert timer_category("kai-shadow-report-oneshot") == "one_shot_expected_inactive"


def test_category_known_recurring_units() -> None:
    assert timer_category("kai-regime-classify") == "recurring_required"
    assert timer_category("kai-health-check") == "recurring_required"


def test_category_unknown_unit_fails_safe_to_recurring() -> None:
    # An unresolvable unit must never be excused as expected-inactive.
    assert timer_category("kai-does-not-exist-xyz") == "recurring_required"


# ── read_latest_timer_audit taxonomy ────────────────────────────────────────


def test_one_shot_inactive_is_not_critical(tmp_path: Path) -> None:
    # The exact audit finding the Runtime-Audit flagged as falsely critical.
    res = read_latest_timer_audit(_write_audit(tmp_path, ["kai-risk-gate-audit-review (inactive)"]))
    assert res["state"] == "ok"
    assert res["severity"] == "ok"
    assert res["critical_count"] == 0
    assert res["expected_inactive_count"] == 1
    assert res["inactive"][0]["category"] == "one_shot_expected_inactive"
    assert res["inactive"][0]["severity"] == "expected_inactive"


def test_recurring_inactive_is_critical(tmp_path: Path) -> None:
    res = read_latest_timer_audit(_write_audit(tmp_path, ["kai-regime-classify (inactive)"]))
    assert res["state"] == "critical"
    assert res["critical_count"] == 1
    assert res["inactive"][0]["severity"] == "critical"


def test_failed_unit_is_critical_even_if_named_one_shot(tmp_path: Path) -> None:
    # systemd-failed is always critical regardless of category.
    res = read_latest_timer_audit(_write_audit(tmp_path, ["kai-risk-gate-audit-review (failed)"]))
    assert res["state"] == "critical"
    assert res["inactive"][0]["severity"] == "critical"


def test_mixed_expected_and_critical(tmp_path: Path) -> None:
    res = read_latest_timer_audit(
        _write_audit(
            tmp_path,
            ["kai-risk-gate-audit-review (inactive)", "kai-regime-classify (inactive)"],
        )
    )
    assert res["state"] == "critical"
    assert res["critical_count"] == 1
    assert res["expected_inactive_count"] == 1


# ── alert policy ─────────────────────────────────────────────────────────────


def test_alert_only_for_critical_recurring(tmp_path: Path) -> None:
    res = read_latest_timer_audit(
        _write_audit(
            tmp_path,
            ["kai-risk-gate-audit-review (inactive)", "kai-regime-classify (inactive)"],
        )
    )
    alerts = timers_warranting_alert(res)
    # one-shot expected-inactive is NOT alerted; only the recurring one is.
    assert alerts == ["kai-regime-classify"]


def test_no_alert_when_only_expected_inactive(tmp_path: Path) -> None:
    res = read_latest_timer_audit(_write_audit(tmp_path, ["kai-shadow-report-oneshot (inactive)"]))
    assert timers_warranting_alert(res) == []
