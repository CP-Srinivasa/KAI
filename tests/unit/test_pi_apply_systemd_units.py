r"""Der Operator-Weg, Unit-Dateien anzuwenden — mit Rueckweg und Beweisen.

Warum es diesen Weg ueberhaupt braucht: seit dem Broker-Vertrag (#739) kopiert
kein passwortfreier Pfad mehr Dateien nach ``/etc/systemd/system``. Der Deploy
misst den Drift nur noch und meldet ``DEPLOY_HOLD``. Anwenden ist
operator-privilegiert — der Dateiinhalt IST das Privileg, und ein Broker, der
beliebige Dateien als root installiert, waere ``NOPASSWD:ALL`` mit Umweg.

Was dieses Skript dem blossen ``sudo cp`` voraus hat, ist genau das, was hier
geprueft wird:

* Es sichert, BEVOR es schreibt. Laesst sich nicht sichern, wird nichts angefasst.
* Es beweist statt zu behaupten: Byte-Gleichheit je Datei, ``active`` je Timer,
  und ein endlicher naechster Termin je Timer. Das Letzte ist der Vorfall vom
  19.08. — ``kai-tv-auto-promote.timer`` stand fuenf Wochen auf enabled+active
  mit ``NextElapseUSecMonotonic=infinity``.
* Scheitert ein Beweis, geht es zurueck.

Und eine Grenze, die beim Testentwurf auffiel und den Code korrigiert hat: eine
wegen Writer-Freeze absichtlich ZURUECKGESTELLTE Unit wird nicht kopiert. Wer
sie trotzdem in den Byte-Beweis nimmt, liest einen gewollten Zustand als
Fehlschlag — und rollt deswegen alles andere zurueck.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "pi_apply_systemd_units.sh"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")

_OLD = "OnBootSec=5min\nOnUnitActiveSec=5min\n"
_NEW = "OnActiveSec=5min\nOnUnitActiveSec=5min\n"


class _Fixture:
    src: Path
    dst: Path
    backups: Path
    systemctl_log: Path
    marker: Path


@pytest.fixture
def units(tmp_path: Path) -> _Fixture:
    fixture = _Fixture()
    fixture.src = tmp_path / "deploy"
    fixture.dst = tmp_path / "etc"
    fixture.backups = tmp_path / "backups"
    fixture.systemctl_log = tmp_path / "systemctl.log"
    # Immer auf einen NICHT existierenden Marker zeigen: sonst wuerde ein echter
    # Freeze im Repo-Arbeitsbaum die Tests still umleiten.
    fixture.marker = tmp_path / "freeze.json"
    fixture.src.mkdir()
    fixture.dst.mkdir()

    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{fixture.systemctl_log.as_posix()}"\n'
        'case "${1:-}" in\n'
        '  is-active) echo "${FAKE_ACTIVE:-active}" ;;\n'
        "  show)\n"
        '    echo "NextElapseUSecRealtime=${FAKE_RT:-0}"\n'
        '    echo "NextElapseUSecMonotonic=${FAKE_MONO:-1755680000000000}"\n'
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    fixture.systemctl = fake  # type: ignore[attr-defined]
    return fixture


def _run(units: _Fixture, *args: str, env: dict[str, str] | None = None):
    assert _BASH is not None
    import os

    merged = dict(os.environ)
    merged.update(
        {
            "PI_UNIT_APPLY_SUDO": "",
            "PI_UNIT_SYNC_SYSTEMCTL": units.systemctl.as_posix(),  # type: ignore[attr-defined]
            "KAI_UNIT_BACKUP_DIR": units.backups.as_posix(),
            "KAI_UNIT_BACKUP_STAMP": "20260820T120000Z",
            "PAPER_WRITER_FREEZE_MARKER": units.marker.as_posix(),
        }
    )
    merged.update(env or {})
    return subprocess.run(  # noqa: S603
        [
            _BASH,
            SCRIPT.as_posix(),
            "--src",
            units.src.as_posix(),
            "--dst",
            units.dst.as_posix(),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=merged,
    )


def _backup_dir(units: _Fixture) -> Path:
    return units.backups / "20260820T120000Z"


# ── Nichts tun ──────────────────────────────────────────────────────────────


def test_identical_state_changes_nothing(units) -> None:
    """Gegenprobe zuerst: ohne sie waere jeder Beweis unten wertlos."""
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_NEW, encoding="utf-8")

    proc = _run(units, "--yes")

    assert proc.returncode == 0
    assert "Nichts zu tun" in proc.stdout
    assert not _backup_dir(units).exists(), "ohne Aenderung darf keine Sicherung entstehen"


def test_dry_run_writes_nothing(units) -> None:
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_OLD, encoding="utf-8")

    proc = _run(units, "--dry-run")

    assert proc.returncode == 0
    assert (units.dst / "kai-x.timer").read_text(encoding="utf-8") == _OLD
    assert not _backup_dir(units).exists()


def test_without_yes_it_asks_and_a_refusal_writes_nothing(units) -> None:
    """Ohne Bestaetigung und ohne TTY darf das Skript NICHT losschreiben."""
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_OLD, encoding="utf-8")

    proc = _run(units)  # stdin ist leer -> read liefert nichts -> Abbruch

    assert proc.returncode == 1
    assert "Abgebrochen" in proc.stdout
    assert (units.dst / "kai-x.timer").read_text(encoding="utf-8") == _OLD


# ── Anwenden und beweisen ───────────────────────────────────────────────────


def test_applies_and_proves_each_file(units) -> None:
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_OLD, encoding="utf-8")

    proc = _run(units, "--yes")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (units.dst / "kai-x.timer").read_text(encoding="utf-8") == _NEW
    assert "beweis: kai-x.timer byte-gleich" in proc.stdout
    assert "beweis: kai-x.timer active mit endlichem naechsten Termin" in proc.stdout


def test_backup_holds_the_original_bytes(units) -> None:
    """Der Rueckweg ist nur so viel wert wie das, was in der Sicherung steht."""
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_OLD, encoding="utf-8")

    _run(units, "--yes")

    assert (_backup_dir(units) / "kai-x.timer").read_text(encoding="utf-8") == _OLD


def test_timer_is_restarted_so_systemd_takes_the_new_schedule(units) -> None:
    """Ohne Restart uebernimmt systemd einen geaenderten Zeitplan nicht."""
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_OLD, encoding="utf-8")

    _run(units, "--yes")

    log = units.systemctl_log.read_text(encoding="utf-8")
    assert "daemon-reload" in log
    assert "restart kai-x.timer" in log


# ── Der Vorfall vom 19.08. als Abnahmekriterium ─────────────────────────────


def test_timer_without_next_trigger_fails_and_rolls_back(units) -> None:
    """Aktiv, byte-gleich — und trotzdem kein Termin. Genau der Vorfall.

    ``kai-tv-auto-promote.timer`` war fuenf Wochen in diesem Zustand. Ein
    Abnahmekriterium, das nur ``is-active`` prueft, haette ihn durchgewinkt.
    """
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_OLD, encoding="utf-8")

    proc = _run(units, "--yes", env={"FAKE_MONO": "infinity", "FAKE_RT": "0"})

    assert proc.returncode == 1
    assert "KEIN naechster Termin" in proc.stderr
    assert (units.dst / "kai-x.timer").read_text(encoding="utf-8") == _OLD, (
        "der Rollback muss die alten Bytes zurueckbringen"
    )


def test_rollback_removes_units_that_did_not_exist_before(units) -> None:
    """Eine neu angelegte Datei muss beim Rueckweg verschwinden, nicht liegen bleiben."""
    (units.src / "kai-new.timer").write_text(_NEW, encoding="utf-8")

    proc = _run(units, "--yes", env={"FAKE_MONO": "infinity", "FAKE_RT": "0"})

    assert proc.returncode == 1
    assert not (units.dst / "kai-new.timer").exists()


def test_inactive_timer_is_also_a_proof_failure(units) -> None:
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_OLD, encoding="utf-8")

    proc = _run(units, "--yes", env={"FAKE_ACTIVE": "failed"})

    assert proc.returncode == 1
    assert "nicht active" in proc.stderr


# ── Zurueckgestellt ist kein Fehlschlag ─────────────────────────────────────


def test_frozen_writer_is_deferred_and_does_not_roll_back_the_rest(units) -> None:
    """Der Fehler, den erst der Testentwurf sichtbar gemacht hat.

    Ein eingefrorener Writer wird bewusst NICHT kopiert. Naehme man ihn in den
    Byte-Beweis, saehe der gewollte Zustand wie ein Fehlschlag aus — und haette
    die daneben liegende, korrekt angewendete Unit mit zurueckgerollt.
    """
    units.marker.write_text(json.dumps({"frozen": True}), encoding="utf-8")
    (units.src / "kai-paper-trading.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-paper-trading.timer").write_text(_OLD, encoding="utf-8")
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_OLD, encoding="utf-8")

    proc = _run(units, "--yes")

    # 10, nicht 0: teils zurueckgestellt ist ein eigener Ausgang. "Fertig" waere
    # gelogen, "gescheitert" waere falsch — beides wuerde den naechsten Leser in
    # die Irre fuehren.
    assert proc.returncode == 10, proc.stdout + proc.stderr
    assert "Zurueckgestellt (Writer-Freeze aktiv): kai-paper-trading.timer" in proc.stdout
    assert (units.dst / "kai-paper-trading.timer").read_text(encoding="utf-8") == _OLD
    assert (units.dst / "kai-x.timer").read_text(encoding="utf-8") == _NEW, (
        "die nicht eingefrorene Unit muss trotzdem angewendet bleiben"
    )


def test_everything_deferred_writes_nothing_at_all(units) -> None:
    units.marker.write_text(json.dumps({"frozen": True}), encoding="utf-8")
    (units.src / "kai-paper-trading.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-paper-trading.timer").write_text(_OLD, encoding="utf-8")

    proc = _run(units, "--yes")

    assert proc.returncode == 10
    assert not _backup_dir(units).exists(), "ohne Schreibvorgang keine Sicherung"
    assert (units.dst / "kai-paper-trading.timer").read_text(encoding="utf-8") == _OLD


def test_invalid_freeze_marker_defers_fail_closed(units) -> None:
    """Unlesbarer Marker heisst verweigern, nicht durchwinken."""
    units.marker.write_text("{kein json", encoding="utf-8")
    (units.src / "kai-paper-trading.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-paper-trading.timer").write_text(_OLD, encoding="utf-8")

    proc = _run(units, "--yes")

    assert proc.returncode == 10
    assert (units.dst / "kai-paper-trading.timer").read_text(encoding="utf-8") == _OLD


# ── Waisen ──────────────────────────────────────────────────────────────────


def test_orphans_are_reported_never_deleted(units) -> None:
    """Eine Unit, die es nur im Ziel gibt, koennte von Hand gesetzt sein."""
    (units.src / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-x.timer").write_text(_NEW, encoding="utf-8")
    (units.dst / "kai-orphan.timer").write_text("OnCalendar=daily\n", encoding="utf-8")

    proc = _run(units, "--yes")

    assert "ORPHAN" in proc.stdout
    assert (units.dst / "kai-orphan.timer").exists()
