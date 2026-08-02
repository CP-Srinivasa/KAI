"""Sprint 6 — SCB (channel.backup) drift monitor (resilience).

The static channel backup must be re-archived whenever channels change. This module
hashes the SCB and compares against a recorded baseline: ``no_baseline`` (first
run, records it), ``stable``, ``changed`` (→ operator re-backup reminder), or
``missing``. Read-only, fail-soft, no capital path.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.lightning_settings import LightningSettings
from app.lightning.backup_monitor import (
    ScbStatus,
    check_scb_drift,
    monitor_scb_once,
    read_scb_status,
)


def test_read_status_missing(tmp_path) -> None:
    s = read_scb_status(tmp_path / "channel.backup")
    assert isinstance(s, ScbStatus) and s.present is False and s.sha256 == ""


def test_read_status_present(tmp_path) -> None:
    p = tmp_path / "channel.backup"
    p.write_bytes(b"scb-bytes-v1")
    s = read_scb_status(p)
    assert s.present is True and s.size_bytes == 12 and len(s.sha256) == 64


def test_drift_no_baseline_records_then_stable(tmp_path) -> None:
    scb = tmp_path / "channel.backup"
    scb.write_bytes(b"v1")
    base = tmp_path / "scb_baseline.json"
    r1 = check_scb_drift(scb, baseline_path=base)
    assert r1["state"] == "no_baseline" and base.exists()
    # second run, unchanged → stable
    r2 = check_scb_drift(scb, baseline_path=base)
    assert r2["state"] == "stable"


def test_drift_changed_updates_baseline(tmp_path) -> None:
    scb = tmp_path / "channel.backup"
    scb.write_bytes(b"v1")
    base = tmp_path / "scb_baseline.json"
    check_scb_drift(scb, baseline_path=base)  # records v1
    scb.write_bytes(b"v2-after-channel-open")  # channel changed → SCB changed
    r = check_scb_drift(scb, baseline_path=base)
    assert r["state"] == "changed"
    assert r["reminder"]  # re-backup reminder surfaced
    # baseline advanced to the new hash → next run is stable again
    assert check_scb_drift(scb, baseline_path=base)["state"] == "stable"


def test_drift_missing_file(tmp_path) -> None:
    base = tmp_path / "scb_baseline.json"
    base.write_text(json.dumps({"sha256": "abc"}), encoding="utf-8")
    r = check_scb_drift(tmp_path / "gone.backup", baseline_path=base)
    assert r["state"] == "missing"


def test_scb_at_exact_max_age_is_stale_and_reminds(tmp_path) -> None:
    scb = tmp_path / "channel.backup"
    scb.write_bytes(b"v1")
    base = tmp_path / "scb_baseline.json"
    observed_at = datetime(2026, 8, 2, 8, 0, tzinfo=UTC)
    os.utime(scb, (observed_at.timestamp(), observed_at.timestamp()))

    first = check_scb_drift(
        scb,
        baseline_path=base,
        max_age_seconds=7200,
        now=observed_at,
    )
    assert first["state"] == "no_baseline"
    assert first["reminder"] is False

    stale = check_scb_drift(
        scb,
        baseline_path=base,
        max_age_seconds=7200,
        now=observed_at + timedelta(hours=2),
    )
    assert stale["state"] == "stale"
    assert stale["reminder"] is True
    assert stale["age_seconds"] == 7200.0


def test_monitor_alerts_on_stale_but_unchanged_fresh_scb_is_quiet(tmp_path) -> None:
    scb = tmp_path / "channel.backup"
    scb.write_bytes(b"v1")
    base = tmp_path / "baseline.json"
    observed_at = datetime(2026, 8, 2, 8, 0, tzinfo=UTC)
    os.utime(scb, (observed_at.timestamp(), observed_at.timestamp()))
    cfg = LightningSettings(
        enabled=True,
        tls_cert_path="test-tls.pem",
        scb_path=str(scb),
        scb_baseline_path=str(base),
        scb_max_age_seconds=7200,
    )
    notifications: list[str] = []

    async def _notify(message: str) -> bool:
        notifications.append(message)
        return True

    fresh = asyncio.run(monitor_scb_once(cfg, notify=_notify, now=observed_at))
    assert fresh["state"] == "no_baseline"
    assert notifications == []

    stable = asyncio.run(
        monitor_scb_once(cfg, notify=_notify, now=observed_at + timedelta(hours=1))
    )
    assert stable["state"] == "stable"
    assert notifications == []

    stale = asyncio.run(monitor_scb_once(cfg, notify=_notify, now=observed_at + timedelta(hours=2)))
    assert stale["state"] == "stale"
    assert stale["alert_sent"] is True
    assert len(notifications) == 1
    assert "SCB" in notifications[0] and "stale" in notifications[0]


def test_scb_settings_are_environment_configurable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_LN_SCB_PATH", str(tmp_path / "channel.backup"))
    monkeypatch.setenv("APP_LN_SCB_BASELINE_PATH", str(tmp_path / "baseline.json"))
    monkeypatch.setenv("APP_LN_SCB_MAX_AGE_SECONDS", "5400")

    cfg = LightningSettings(_env_file=None)
    assert cfg.scb_path == str(tmp_path / "channel.backup")
    assert cfg.scb_baseline_path == str(tmp_path / "baseline.json")
    assert cfg.scb_max_age_seconds == 5400


def test_systemd_timer_is_installable_and_runs_hourly() -> None:
    root = Path(__file__).resolve().parents[2]
    service = (root / "deploy/systemd/kai-ln-scb-monitor.service").read_text(encoding="utf-8")
    timer = (root / "deploy/systemd/kai-ln-scb-monitor.timer").read_text(encoding="utf-8")
    installer = (root / "scripts/pi_install_systemd.sh").read_text(encoding="utf-8")

    assert "python -m app.lightning.backup_monitor" in service
    assert "OnUnitActiveSec=1h" in timer
    assert '"kai-ln-scb-monitor.service"' in installer
    assert '"kai-ln-scb-monitor.timer"' in installer
