r"""Der Unit-Apply-Beweis darf einen laufenden Timer-Lauf nicht als Defekt lesen.

Vorfall 2026-08-21, millisekundengenau aus dem Journal:

    08:11:16.848  Timer neu gestartet (Apply)
    08:11:16.859  Service startet — Persistent=true holt den seit dem 12.07.
                  verpassten Kalenderlauf nach
    08:11:20.03   Beweis misst den Timer MITTEN im Lauf -> NextElapse leer
                  -> "FEHLGESCHLAGEN" -> Rollback ALLER 30 Units
    08:11:20.34   Lauf faehrt normal zu Ende (2093 Events geprueft)

Die Reparatur hat funktioniert; verworfen hat sie der Beweis. Waehrend der
ausgeloeste Service laeuft, hat ``OnUnitActiveSec`` nichts zum Ankern und ein
gerade nachgeholter ``Persistent=``-Lauf haelt den Timer ebenso kurz terminlos —
in beiden Faellen ist "kein naechster Termin" der Normalzustand, nicht der
Befund. Derselbe Klassenfehler wurde am selben Tag im Waechter behoben (#748);
hier sass er ein zweites Mal.

Der ECHTE Befund muss weiterhin durchschlagen: kein Termin UND kein laufender
Service ist genau der Zustand, der fuenf Wochen unbemerkt blieb.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "pi_apply_systemd_units.sh"

_UNIT = "kai-probe-fixture.timer"
_SERVICE = "kai-probe-fixture.service"


def _fake_systemctl(tmp_path: Path, body: str) -> Path:
    """Ein systemctl-Ersatz, der genau die abgefragten Properties beantwortet."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake = fake_bin / "systemctl"
    fake.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    fake.chmod(0o755)
    return fake_bin


def _run(tmp_path: Path, fake_bin: Path) -> subprocess.CompletedProcess[str]:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir(exist_ok=True)
    dst.mkdir(exist_ok=True)
    (src / _UNIT).write_text("[Timer]\nOnCalendar=*:0/5\nPersistent=true\n", encoding="utf-8")
    (dst / _UNIT).write_text("[Timer]\nOnBootSec=3min\n", encoding="utf-8")

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "PI_UNIT_APPLY_SRC": str(src),
        "PI_UNIT_APPLY_DST": str(dst),
        "PI_UNIT_APPLY_SUDO": "",
        "PI_UNIT_SYNC_SYSTEMCTL": str(fake_bin / "systemctl"),
        "KAI_UNIT_BACKUP_DIR": str(tmp_path / "backup"),
        "KAI_UNIT_PROOF_WAIT_S": "10",
    }
    return subprocess.run(
        ["bash", str(_SCRIPT), "--yes"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )


@pytest.fixture(autouse=True)
def _needs_bash() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash unavailable")


# Erste zwei Abfragen: Lauf im Gange (kein Termin, Service activating).
# Danach: Lauf beendet, Termin da. Genau die Sequenz vom 2026-08-21.
_RUN_THEN_SETTLE = f"""
counter="$TMPDIR_COUNTER"
n=$(cat "$counter" 2>/dev/null || echo 0)
case "$1 $2" in
  "show {_UNIT}")
      n=$((n + 1)); echo "$n" > "$counter"
      if [ "$n" -le 2 ]; then
          echo "Unit={_SERVICE}"
          echo "NextElapseUSecRealtime="
          echo "NextElapseUSecMonotonic=infinity"
      else
          echo "Unit={_SERVICE}"
          echo "NextElapseUSecRealtime=Fri 2026-08-21 10:15:00 UTC"
          echo "NextElapseUSecMonotonic=0"
      fi
      ;;
  "show {_SERVICE}")
      if [ "$n" -le 2 ]; then echo "ActiveState=activating"; else echo "ActiveState=inactive"; fi
      ;;
  *) ;;
esac
if [ "$1" = "is-active" ]; then echo active; fi
exit 0
"""

# Kein Termin und KEIN laufender Service — der echte Vorfall.
_TRULY_DEAD = f"""
case "$1 $2" in
  "show {_UNIT}")
      echo "Unit={_SERVICE}"
      echo "NextElapseUSecRealtime="
      echo "NextElapseUSecMonotonic=infinity"
      ;;
  "show {_SERVICE}") echo "ActiveState=inactive" ;;
  *) ;;
esac
if [ "$1" = "is-active" ]; then echo active; fi
exit 0
"""


def test_running_catchup_does_not_trigger_rollback(tmp_path: Path) -> None:
    """Der Lauf laeuft noch — der Beweis wartet ihn ab statt zurueckzurollen."""
    os.environ["TMPDIR_COUNTER"] = str(tmp_path / "counter")
    fake_bin = _fake_systemctl(tmp_path, _RUN_THEN_SETTLE)

    proc = _run(tmp_path, fake_bin)

    assert "Rollback" not in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "dst" / _UNIT).read_text(encoding="utf-8").startswith("[Timer]\nOnCalendar")


def test_truly_scheduleless_timer_still_fails(tmp_path: Path) -> None:
    """Kein Termin OHNE laufenden Service bleibt ein Fehlschlag mit Rollback.

    Sonst haette der Fix den Beweis entwertet, den es zu erhalten gilt.
    """
    os.environ["TMPDIR_COUNTER"] = str(tmp_path / "counter")
    fake_bin = _fake_systemctl(tmp_path, _TRULY_DEAD)

    proc = _run(tmp_path, fake_bin)

    combined = proc.stdout + proc.stderr
    assert "KEIN naechster Termin" in combined, combined
    assert "Rollback" in combined, combined
    assert proc.returncode == 1, combined
    assert (tmp_path / "dst" / _UNIT).read_text(encoding="utf-8").startswith("[Timer]\nOnBootSec")
