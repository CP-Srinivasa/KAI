"""Unit tests for the Timer Health Reader (DALI-P-101)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.timer_health import (
    read_latest_timer_audit,
    timer_health_freshness_contract,
)


def test_timer_health_empty_file(tmp_path: Path) -> None:
    """NEGATIVE CONTROL (STAB-2026-09-01 §11): no snapshot => UNKNOWN, never OK.

    The previous contract returned ``total = active = <fallback constant>``, i.e.
    it manufactured a fully-healthy reading out of thin air while no measurement
    existed at all. A missing producer output is now unambiguously unknown.
    """
    missing_file = tmp_path / "missing.jsonl"
    res = read_latest_timer_audit(missing_file)
    assert res["state"] == "no_data"
    assert res["checked_at"] is None
    assert res["stale_minutes"] is None
    # The decisive assertion: no fabricated healthy count.
    assert res["counts_are_current"] is False
    assert res["total"] is None
    assert res["active"] is None
    assert res["status_reason"] == "NO_SNAPSHOT"
    assert res["inactive"] == []
    # The installed inventory is still knowable — it comes from the repo, not
    # from the (absent) measurement — and must be the real fleet, not 10.
    assert res["installed_timer_count"] >= 50

    empty_file = tmp_path / "empty.jsonl"
    empty_file.touch()
    res2 = read_latest_timer_audit(empty_file)
    assert res2["state"] == "no_data"
    assert res2["total"] is None


def test_timer_health_all_active(tmp_path: Path) -> None:
    # all_active: checked_at=now, inactive=[], explicit total_timers/active_timers
    audit_file = tmp_path / "timer_health.jsonl"
    t_now = datetime.now(UTC).isoformat()

    r = {
        "timestamp_utc": t_now,
        "event": "timer_health_probe.ok",
        "findings": [],
        "total_timers": 8,
        "active_timers": 8,
    }
    with audit_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(r) + "\n")

    res = read_latest_timer_audit(audit_file)
    assert res["state"] == "ok"
    assert res["checked_at"] == t_now
    assert res["stale_minutes"] == 0
    # POSITIVE CONTROL: a fresh snapshot reports current counts.
    assert res["counts_are_current"] is True
    assert res["total"] == 8
    assert res["active"] == 8
    assert res["last_known_total"] == 8
    assert res["inactive"] == []


def test_timer_health_recurring_inactive_is_critical(tmp_path: Path) -> None:
    # FS-2 (#198): two RECURRING timers inactive -> state="critical" (not the old
    # blanket "has_inactive"). kai-auto-annotate (OnBootSec) + kai-pi-health
    # (wildcard OnCalendar) are both recurring_required.
    audit_file = tmp_path / "timer_health.jsonl"
    t_now = datetime.now(UTC).isoformat()

    r = {
        "timestamp_utc": t_now,
        "event": "timer_health_probe.findings",
        "findings": ["kai-auto-annotate.timer (inactive)", "kai-pi-health.timer (inactive)"],
        "total_timers": 8,
        "active_timers": 6,
    }
    with audit_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(r) + "\n")

    res = read_latest_timer_audit(audit_file)
    assert res["state"] == "critical"
    assert res["severity"] == "critical"
    assert res["critical_count"] == 2
    assert res["expected_inactive_count"] == 0
    assert len(res["inactive"]) == 2
    assert res["inactive"][0]["unit"] == "kai-auto-annotate.timer"
    assert res["inactive"][0]["category"] == "recurring_required"
    assert res["inactive"][0]["severity"] == "critical"
    assert res["inactive"][1]["unit"] == "kai-pi-health.timer"
    assert res["total"] == 8
    assert res["active"] == 6


def _write_snapshot(path: Path, *, age_seconds: float, **extra: object) -> None:
    stamp = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
    row: dict[str, object] = {
        "timestamp_utc": stamp,
        "event": "timer_health_probe.ok",
        "findings": [],
        "total_timers": 8,
        "active_timers": 8,
        "monitored_timers": 8,
    }
    row.update(extra)
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def test_freshness_budget_is_derived_from_the_producer_cadence() -> None:
    """STAB-2026-09-01 §12: no flat 2h rule.

    ``kai-pi-health.timer`` runs ``OnCalendar=*-*-* 04:30:00 UTC`` — once a day.
    Judging its output against a 2h budget marked the snapshot stale for 22 of
    every 24 hours: a structural false positive, not a measurement. The budget
    must therefore exceed a full cadence.
    """
    contract = timer_health_freshness_contract()
    assert contract.expected_cadence_seconds == 86_400
    assert contract.stale_after_seconds is not None
    assert contract.stale_after_seconds > 86_400
    # And it must be built from the declared parts, not a magic number.
    assert contract.stale_after_seconds == (
        contract.expected_cadence_seconds
        + contract.accuracy_seconds
        + contract.runtime_margin_seconds
        + contract.grace_seconds
    )


def test_timer_health_within_cadence_is_not_stale(tmp_path: Path) -> None:
    """NEGATIVE CONTROL: 3h old under a daily cadence is FRESH, not stale.

    This is the exact case the old ``> 7200`` rule got wrong every single day.
    """
    audit_file = tmp_path / "timer_health.jsonl"
    _write_snapshot(audit_file, age_seconds=3 * 3600)

    res = read_latest_timer_audit(audit_file)
    assert res["state"] == "ok"
    assert res["counts_are_current"] is True
    assert res["total"] == 8


def test_timer_health_stale(tmp_path: Path) -> None:
    """POSITIVE CONTROL: past the derived budget => stale AND counts go UNKNOWN.

    §11: a stale snapshot may never report "8 OK". The observed numbers survive
    only as ``last_known_*``.
    """
    audit_file = tmp_path / "timer_health.jsonl"
    contract = timer_health_freshness_contract()
    assert contract.stale_after_seconds is not None
    _write_snapshot(audit_file, age_seconds=contract.stale_after_seconds + 3600)

    res = read_latest_timer_audit(audit_file)
    assert res["state"] == "stale"
    assert res["status_reason"] == "SNAPSHOT_OLDER_THAN_CADENCE"
    # THE regression this whole section exists for:
    assert res["counts_are_current"] is False
    assert res["total"] is None
    assert res["active"] is None
    assert res["last_known_total"] == 8
    assert res["last_known_active"] == 8


def test_timer_health_corrupt_fallback(tmp_path: Path) -> None:
    # corrupt last line + valid second-to-last -> state="corrupt" mit Fallback-Daten
    audit_file = tmp_path / "timer_health.jsonl"
    t_now = datetime.now(UTC).isoformat()

    r_valid = {
        "timestamp_utc": t_now,
        "event": "timer_health_probe.findings",
        "findings": ["kai-auto-annotate.timer (inactive)"],
        "total_timers": 8,
        "active_timers": 7,
    }

    with audit_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(r_valid) + "\n")
        f.write("corrupt line that cannot be parsed as json\n")

    res = read_latest_timer_audit(audit_file)
    assert res["state"] == "corrupt"
    assert len(res["inactive"]) == 1
    assert res["inactive"][0]["unit"] == "kai-auto-annotate.timer"
    # §11: a corrupt tail means the reading is not trustworthy as CURRENT state.
    assert res["counts_are_current"] is False
    assert res["total"] is None
    assert res["active"] is None
    assert res["last_known_total"] == 8
    assert res["last_known_active"] == 7


def test_installed_inventory_is_the_real_fleet_not_the_fallback_ten() -> None:
    """STAB-2026-09-01 §13: "10 Timer" was a path bug, not a curated subset.

    ``_get_default_total`` resolved ``deploy/systemd`` via ``parents[3]``, which
    for a module at ``app/services/`` is the directory ABOVE the repo root. The
    lookup always missed, the bare ``except`` swallowed it, and the hard-coded
    fallback 10 reached the dashboard while 56 kai-*.timer units were installed.
    """
    from app.services.timer_health import _get_default_total, installed_timer_units

    units = installed_timer_units()
    assert len(units) >= 50, "deploy/systemd must resolve from app/services/"
    assert all(u.startswith("kai-") and u.endswith(".timer") for u in units)
    assert _get_default_total() == len(units)
