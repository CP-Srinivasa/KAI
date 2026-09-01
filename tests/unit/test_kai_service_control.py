"""Der Service-Broker ist der einzige passwortfreie Root-Pfad — er muss dicht sein.

P0 2026-08-19: die Vorgaenger-Regel `NOPASSWD: /usr/bin/systemctl restart kai-*`
war umgehbar. sudoers matcht Argumente als EINEN String, `*` matcht auch
Leerzeichen — `systemctl restart kai-x.service ssh.service` wurde damit
autorisiert (live verifiziert). Die Validierung liegt jetzt im Broker, und diese
Tests sind die Gegenprobe: sie fahren das Skript wirklich, gegen ein gefaketes
``systemctl`` im PATH, und pruefen Rueckgabecode UND die tatsaechlich
weitergereichte Kommandozeile.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

BROKER = Path(__file__).resolve().parents[2] / "deploy" / "bin" / "kai-service-control"

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="Broker ist ein POSIX-Shell-Skript (laeuft in CI auf Linux)",
)

# Was das gefakete systemctl fuer `show -p User --value <unit>` antwortet.
_FAKE_SYSTEMCTL = """#!/bin/bash
printf '%s\\n' "$*" >> "$CALL_LOG"
if [ "$1" = "show" ]; then
    case "${!#}" in
        kai-standby-data.service) printf '\\n' ;;          # root-Unit: kein User=
        kai-unknown.service)      printf '\\n' ;;          # unbekannte Unit
        kai-*)                    printf 'ubuntu\\n' ;;
        *)                        printf '\\n' ;;
    esac
    exit 0
fi
exit 0
"""


@pytest.fixture
def run(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "systemctl"
    fake.write_text(_FAKE_SYSTEMCTL, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    call_log = tmp_path / "calls.txt"
    call_log.write_text("", encoding="utf-8")

    def _run(*args: str) -> tuple[int, str, list[str]]:
        env = dict(os.environ)
        env["PATH"] = f"{bindir}:{env['PATH']}"
        env["CALL_LOG"] = str(call_log)
        proc = subprocess.run(
            ["bash", str(BROKER), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        calls = [ln for ln in call_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return proc.returncode, proc.stderr, calls

    return _run


# --- erlaubt --------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["start", "stop", "restart", "reload", "status"])
def test_erlaubte_verben_auf_ubuntu_unit(run, verb: str) -> None:
    code, err, calls = run(verb, "kai-server.service")
    assert code == 0, err
    # Genau EIN Steuer-Aufruf, mit genau EINER Unit.
    assert f"{verb} kai-server.service" in calls[-1]


def test_instanz_unit_ist_erlaubt(run) -> None:
    code, err, _ = run("restart", "kai-unit-failure-notify@kai-server.service.service")
    assert code == 0, err


# --- DER Bypass, gegen den dieser Broker gebaut wurde ---------------------------


def test_zweite_unit_wird_abgewiesen(run) -> None:
    """`systemctl restart kai-x.service ssh.service` — genau der Live-Bypass."""
    code, err, calls = run("restart", "kai-server.service", "ssh.service")
    assert code != 0
    assert "genau 2 Argumente" in err
    assert calls == [], "es darf ueberhaupt kein systemctl-Aufruf passieren"


def test_zwei_units_in_einem_argument_werden_abgewiesen(run) -> None:
    """Auch als EIN Argument mit Leerzeichen — so matchte das sudoers-Glob."""
    code, err, calls = run("restart", "kai-server.service ssh.service")
    assert code != 0
    assert "unit-Name nicht erlaubt" in err
    assert calls == []


def test_dritte_und_weitere_units_werden_abgewiesen(run) -> None:
    code, _, calls = run("restart", "kai-server.service", "zzz1.service", "zzz2.service")
    assert code != 0
    assert calls == []


@pytest.mark.parametrize("opt", ["--no-block", "--machine=.", "--root=/", "--user"])
def test_optionen_werden_abgewiesen(run, opt: str) -> None:
    code, _, calls = run("restart", f"kai-server.service {opt}")
    assert code != 0
    assert calls == []


# --- weitere Absicherungen ------------------------------------------------------


@pytest.mark.parametrize("verb", ["daemon-reload", "mask", "link", "edit", "cat", "-h", ""])
def test_fremde_verben_werden_abgewiesen(run, verb: str) -> None:
    code, _, calls = run(verb, "kai-server.service")
    assert code != 0
    assert calls == []


@pytest.mark.parametrize(
    "unit",
    [
        "ssh.service",
        "kai-server",  # ohne .service
        "kai-../../etc/passwd.service",
        "/etc/systemd/system/kai-server.service",
        "kai-*.service",
        "kai-server.service;id",
        "$(id).service",
        "",
    ],
)
def test_unzulaessige_unit_namen(run, unit: str) -> None:
    code, _, calls = run("restart", unit)
    assert code != 0
    assert calls == [] or all("show" in c for c in calls)


def test_root_unit_bleibt_passwortpflichtig(run) -> None:
    """Die eigentliche Absicht: Root-Units nie passwortfrei steuerbar.

    Damit muss NICHT bewiesen werden, dass jede Root-Unit heute und kuenftig
    sicher konfiguriert ist (ExecStartPre/Post, EnvironmentFile, Drop-ins,
    Wrapper wie `/usr/bin/bash /home/ubuntu/...`).
    """
    code, err, calls = run("restart", "kai-standby-data.service")
    assert code != 0
    assert "root" in err.lower() or "ubuntu" in err
    assert not any("restart" in c for c in calls)


def test_unbekannte_unit_wird_abgewiesen(run) -> None:
    code, _, calls = run("restart", "kai-unknown.service")
    assert code != 0
    assert not any("restart" in c for c in calls)


def test_zu_wenige_argumente(run) -> None:
    code, err, calls = run("restart")
    assert code != 0
    assert "genau 2 Argumente" in err
    assert calls == []


# --- Eigenschaften des Skripts selbst -------------------------------------------


def test_broker_ist_fail_closed_geschrieben() -> None:
    text = BROKER.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/bash")
    assert "set -euo pipefail" in text
    # Die Unit darf nie unquoted expandiert werden.
    assert "$unit" not in text.replace('"$unit"', "").replace("'$unit'", "")
    # Kein $* / $@ an systemctl — sonst waere die Argumentpruefung wirkungslos.
    assert "systemctl $*" not in text
    assert 'systemctl "$@"' not in text
