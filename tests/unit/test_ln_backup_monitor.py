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
    assert "python -m app.lightning.backup_monitor" in service
    assert "OnUnitActiveSec=1h" in timer
    # Installierbarkeit haengt seit 2026-08-18 an der EXISTENZ der Unit-Datei,
    # nicht mehr an einem Namenseintrag im Skript: die Kopierliste wird aus
    # deploy/systemd/ abgeleitet (vorher fehlten 59 von 113 Units).
    for name in ("kai-ln-scb-monitor.service", "kai-ln-scb-monitor.timer"):
        assert (root / "deploy/systemd" / name).exists()


def _enable_on_install_block(installer: str) -> str:
    start = installer.index("ENABLE_ON_INSTALL=(")
    return installer[start : installer.index("\n)\n", start)]


def test_scb_monitor_timer_is_installed_but_not_auto_enabled() -> None:
    """Der Timer darf NICHT beim Installer-Lauf scharf geschaltet werden.

    Befund 2026-08-02 auf dem Pi: ``APP_LN_SCB_PATH`` ist ungesetzt und es
    existiert dort ueberhaupt keine ``channel.backup`` — der Off-node-Pull laeuft
    per Operator-Entscheid "Weg A" (14.07.) auf der WORKSTATION, nicht auf dem Pi,
    ausdruecklich um keinen weiteren SSH-Trust zum LN-Node aufzumachen.

    Mit leerem ``scb_path`` lieferte ``monitor_scb_once`` ``reminder=True`` und
    ``main()`` beendete sich mit 1. Bei ``OnUnitActiveSec=1h`` waeren das 24
    Telegram-Alerts pro Tag plus eine dauerhaft ``failed`` Unit — dieselbe
    Fehlerklasse wie die 347 False-FAIL-Alerts vom 12.07.

    Der Timer wird deshalb installiert, aber bewusst erst nach Konfiguration
    aktiviert — gleiche Behandlung wie ``kai-funding-refresh.timer``.
    """
    root = Path(__file__).resolve().parents[2]
    installer = (root / "scripts/pi_install_systemd.sh").read_text(encoding="utf-8")
    enable_block = _enable_on_install_block(installer)

    assert '"kai-ln-scb-monitor.timer"' not in enable_block
    # Gegenprobe: der Block wurde tatsaechlich gefunden und ist nicht leer.
    assert '"kai-recalc-cycle.timer"' in enable_block


def test_unconfigured_scb_path_does_not_alert() -> None:
    """Ein unkonfigurierter Monitor ist kein Alarmgrund, sondern eine Journal-Zeile."""
    cfg = LightningSettings(enabled=True, tls_cert_path="test-tls.pem", scb_path="")

    report = asyncio.run(monitor_scb_once(cfg, notify=None))

    assert report["state"] == "not_configured"
    assert report["reminder"] is False
    assert report["alert_sent"] is False


def test_configured_but_absent_scb_still_alerts() -> None:
    """Gegenprobe: ein KONFIGURIERTER, aber fehlender SCB bleibt ein echter Alarm."""
    sent: list[str] = []

    async def _notify(text: str) -> bool:
        sent.append(text)
        return True

    cfg = LightningSettings(
        enabled=True, tls_cert_path="test-tls.pem", scb_path="/nonexistent/channel.backup"
    )

    report = asyncio.run(monitor_scb_once(cfg, notify=_notify))

    assert report["state"] == "missing"
    assert report["reminder"] is True
    assert len(sent) == 1
