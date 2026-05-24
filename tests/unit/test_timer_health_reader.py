"""Unit tests for the Timer Health Reader (DALI-P-101)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.timer_health import read_latest_timer_audit


def test_timer_health_empty_file(tmp_path: Path) -> None:
    # Empty file or missing file -> state="no_data", dynamic total from deploy/systemd
    missing_file = tmp_path / "missing.jsonl"
    res = read_latest_timer_audit(missing_file)
    assert res["state"] == "no_data"
    assert res["checked_at"] is None
    assert res["stale_minutes"] is None
    assert res["total"] >= 10
    assert res["active"] == res["total"]
    assert res["inactive"] == []

    empty_file = tmp_path / "empty.jsonl"
    empty_file.touch()
    res2 = read_latest_timer_audit(empty_file)
    assert res2["state"] == "no_data"


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
    assert res["total"] == 8
    assert res["active"] == 8
    assert res["inactive"] == []


def test_timer_health_has_inactive(tmp_path: Path) -> None:
    # has_inactive: 2 inactive units -> state="has_inactive", explicit total/active
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
    assert res["state"] == "has_inactive"
    assert len(res["inactive"]) == 2
    assert res["inactive"][0]["unit"] == "kai-auto-annotate.timer"
    assert res["inactive"][0]["state"] == "inactive"
    assert res["inactive"][1]["unit"] == "kai-pi-health.timer"
    assert res["inactive"][1]["state"] == "inactive"
    assert res["total"] == 8
    assert res["active"] == 6


def test_timer_health_stale(tmp_path: Path) -> None:
    # stale: checked_at=now-3h, inactive=[] -> state="stale", using dynamic total
    audit_file = tmp_path / "timer_health.jsonl"
    t_stale = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

    r = {
        "timestamp_utc": t_stale,
        "event": "timer_health_probe.ok",
        "findings": [],
    }
    with audit_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(r) + "\n")

    res = read_latest_timer_audit(audit_file)
    assert res["state"] == "stale"
    assert res["stale_minutes"] >= 180
    assert res["total"] >= 10


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
    assert res["total"] == 8
    assert res["active"] == 7
