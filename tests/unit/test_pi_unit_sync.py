"""Tests für den Unit-Sync im Deploy-Pfad.

Befund 2026-08-18, zweimal an einem Tag: `kai_deploy.sh` zieht den Checkout per
ff-merge nach, fasst Unit-Dateien aber nicht an. Beide Kadenz-Änderungen des
Tages (`kai-premium-healthcheck` 60→300 s, `kai-oracle-earnings-booking`
10→60 min) waren nach dem Deploy committet, aber NICHT live — erst ein manuelles
`cp` + `daemon-reload` hat sie wirksam gemacht. Ohne diesen Handgriff hätte das
Repo eine Kadenz behauptet, die auf dem Pi nie galt.

Die Grenzen sind hier wichtiger als die Funktion, weil das Skript mit sudo auf
einem laufenden System arbeitet:

* `.service` wird NIE neu gestartet. Ein Deploy-Restart mitten im Tick hat am
  17.08. `kai-paper-trading` mit SIGTERM getötet und über `OnFailure=` einen
  Fehlalarm ausgelöst.
* `.timer` MUSS neu gestartet werden, sonst übernimmt systemd den Zeitplan
  nicht — und genau das ist der Fehler, den der Sync beheben soll.
* Ein eingefrorener Writer wird gar nicht angefasst: weder kopiert noch neu
  gestartet. Halb angewendet (Datei neu, Zeitplan alt) wäre schlimmer als gar
  nicht, weil es unsichtbar ist.
* Units, die es nur in `/etc` gibt, werden gemeldet, nie gelöscht.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "lib" / "pi_unit_sync.sh"
FREEZE = REPO / "scripts" / "lib" / "paper_writer_freeze.sh"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run(  # noqa: S603
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def _fake_systemctl(tmp_path: Path) -> Path:
    """Protokolliert Aufrufe, statt echtes systemd anzufassen."""
    log = tmp_path / "systemctl.log"
    fake = tmp_path / "systemctl"
    fake.write_text(
        '#!/usr/bin/env bash\necho "$@" >> "' + log.as_posix() + '"\nexit 0\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _run_apply(tmp_path: Path, src: Path, dst: Path, extra: str = "") -> tuple[str, int, str]:
    fake = _fake_systemctl(tmp_path)
    script = (
        f'set -uo pipefail; . "{HELPER.as_posix()}"; {extra} '
        f'PI_UNIT_SYNC_SUDO="" PI_UNIT_SYNC_SYSTEMCTL="{fake.as_posix()}" '
        f'pi_unit_sync_apply "{src.as_posix()}" "{dst.as_posix()}"; echo "RC=$?"'
    )
    proc = _bash(script)
    log = tmp_path / "systemctl.log"
    return (
        proc.stdout + proc.stderr,
        proc.returncode,
        (log.read_text(encoding="utf-8") if log.exists() else ""),
    )


def _units(tmp_path: Path, src_files: dict[str, str], dst_files: dict[str, str]):
    src = tmp_path / "deploy"
    dst = tmp_path / "etc"
    src.mkdir()
    dst.mkdir()
    for name, body in src_files.items():
        (src / name).write_text(body, encoding="utf-8")
    for name, body in dst_files.items():
        (dst / name).write_text(body, encoding="utf-8")
    return src, dst


def test_helper_defines_functions_without_side_effects() -> None:
    """Sourcen darf nichts tun — wie beim Freeze-Helper."""
    out = _bash(f'. "{HELPER.as_posix()}" && echo SOURCED_OK')
    assert "SOURCED_OK" in out.stdout
    assert out.returncode == 0


def test_diff_reports_changed_new_and_orphan(tmp_path: Path) -> None:
    src, dst = _units(
        tmp_path,
        {"kai-a.timer": "neu\n", "kai-b.service": "gleich\n"},
        {"kai-a.timer": "alt\n", "kai-b.service": "gleich\n", "kai-weg.timer": "x\n"},
    )
    out = _bash(f'. "{HELPER.as_posix()}"; pi_unit_sync_diff "{src.as_posix()}" "{dst.as_posix()}"')

    lines = set(out.stdout.split())
    assert "DIFF" in lines and "kai-a.timer" in lines
    assert "ORPHAN" in lines and "kai-weg.timer" in lines
    assert "kai-b.service" not in lines, "unveraenderte Unit darf nicht auftauchen"


def test_changed_timer_is_copied_and_restarted(tmp_path: Path) -> None:
    """Der Realfall: geänderte Kadenz muss auch im laufenden systemd ankommen."""
    src, dst = _units(
        tmp_path,
        {"kai-oracle-earnings-booking.timer": "OnUnitActiveSec=60min\n"},
        {"kai-oracle-earnings-booking.timer": "OnUnitActiveSec=10min\n"},
    )
    out, _, calls = _run_apply(tmp_path, src, dst)

    assert (dst / "kai-oracle-earnings-booking.timer").read_text(
        encoding="utf-8"
    ) == "OnUnitActiveSec=60min\n"
    assert "daemon-reload" in calls
    assert "restart kai-oracle-earnings-booking.timer" in calls
    assert "kopiert" in out


def test_changed_service_is_copied_but_never_restarted(tmp_path: Path) -> None:
    """SIGTERM-Lehre vom 17.08.: ein Deploy startet keinen Dienst mitten im Tick."""
    src, dst = _units(
        tmp_path,
        {"kai-paper-trading.service": "ExecStart=/neu\n"},
        {"kai-paper-trading.service": "ExecStart=/alt\n"},
    )
    out, _, calls = _run_apply(tmp_path, src, dst)

    assert (dst / "kai-paper-trading.service").read_text(encoding="utf-8") == "ExecStart=/neu\n"
    assert "daemon-reload" in calls
    assert "restart" not in calls, "Dienst wurde neu gestartet — genau das darf nicht passieren"
    assert "KEIN Restart" in out


def test_orphan_in_etc_is_reported_never_deleted(tmp_path: Path) -> None:
    src, dst = _units(tmp_path, {}, {"kai-alt.timer": "x\n"})
    out, _, _ = _run_apply(tmp_path, src, dst)

    assert (dst / "kai-alt.timer").exists(), "Unit wurde geloescht"
    assert "ORPHAN" in out


def test_new_unit_is_copied_but_not_enabled(tmp_path: Path) -> None:
    src, dst = _units(tmp_path, {"kai-neu.timer": "x\n"}, {})
    out, _, calls = _run_apply(tmp_path, src, dst)

    assert (dst / "kai-neu.timer").exists()
    assert "enable" not in calls, "Enable ist Sache von pi_install_systemd.sh"
    assert "kopiert" in out


def test_no_change_does_nothing(tmp_path: Path) -> None:
    src, dst = _units(tmp_path, {"kai-a.timer": "gleich\n"}, {"kai-a.timer": "gleich\n"})
    out, rc, calls = _run_apply(tmp_path, src, dst)

    assert "keine Abweichung" in out
    assert calls == "", "ohne Abweichung darf systemctl nicht angefasst werden"
    assert "RC=0" in out


def test_dry_run_touches_nothing(tmp_path: Path) -> None:
    src, dst = _units(tmp_path, {"kai-a.timer": "neu\n"}, {"kai-a.timer": "alt\n"})
    out, _, calls = _run_apply(tmp_path, src, dst, extra="PI_UNIT_SYNC_DRY_RUN=1")

    assert (dst / "kai-a.timer").read_text(encoding="utf-8") == "alt\n"
    assert calls == ""
    assert "WUERDE kopieren" in out


def test_frozen_writer_timer_is_not_copied_at_all(tmp_path: Path) -> None:
    """Halb angewendet waere schlimmer als gar nicht.

    Datei neu + Zeitplan alt sieht auf `systemctl cat` korrekt aus und laeuft
    trotzdem falsch — also lieber sichtbar zurueckstellen.
    """
    src, dst = _units(
        tmp_path,
        {"kai-paper-trading.timer": "OnUnitActiveSec=1min\n"},
        {"kai-paper-trading.timer": "OnUnitActiveSec=10min\n"},
    )
    fake = _fake_systemctl(tmp_path)
    # Freeze-Helper mitsourcen und Freeze erzwingen: der Guard verweigert dann
    # jeden geschuetzten Writer.
    script = (
        f'set -uo pipefail; . "{FREEZE.as_posix()}"; . "{HELPER.as_posix()}"; '
        "paper_writer_freeze_guard_restart() { return 10; }; "
        f'PI_UNIT_SYNC_SUDO="" PI_UNIT_SYNC_SYSTEMCTL="{fake.as_posix()}" '
        f'pi_unit_sync_apply "{src.as_posix()}" "{dst.as_posix()}"; echo "RC=$?"'
    )
    proc = _bash(script)
    out = proc.stdout + proc.stderr
    log = tmp_path / "systemctl.log"
    calls = log.read_text(encoding="utf-8") if log.exists() else ""

    assert (dst / "kai-paper-trading.timer").read_text(
        encoding="utf-8"
    ) == "OnUnitActiveSec=10min\n", "eingefrorener Writer wurde trotzdem ueberschrieben"
    assert "restart kai-paper-trading.timer" not in calls
    assert "ZURUECKGESTELLT" in out
    assert "RC=10" in out


def test_backup_files_in_etc_are_not_reported_as_orphans(tmp_path: Path) -> None:
    """Auf der Pi liegen acht `.bak*`-Sicherungen aus fruehreren Handeingriffen.

    systemd ignoriert sie (keine gueltige Unit-Endung). Sie bei JEDEM Deploy als
    ORPHAN zu melden waere genau das Alarm-Rauschen, das ein Operator nach drei
    Deploys nicht mehr liest — und dann faellt die echte Meldung mit durch.
    """
    src, dst = _units(
        tmp_path,
        {"kai-a.timer": "x\n"},
        {
            "kai-a.timer": "x\n",
            "kai-paper-trading.timer.bak-reqfix-20260624_084846": "alt\n",
            "kai-tg-listener.service.bak.20260511-pre-nb1": "alt\n",
            "kai-echt-verwaist.timer": "y\n",
        },
    )
    out = _bash(f'. "{HELPER.as_posix()}"; pi_unit_sync_diff "{src.as_posix()}" "{dst.as_posix()}"')

    assert "kai-echt-verwaist.timer" in out.stdout, "echter Waise muss gemeldet werden"
    assert ".bak" not in out.stdout, "Sicherungskopien duerfen kein ORPHAN erzeugen"
