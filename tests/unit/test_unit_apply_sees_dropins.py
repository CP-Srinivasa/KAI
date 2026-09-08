"""Byte-Gleichheit der ``.service``-Datei beweist nicht, welcher Code startet.

Gemessen am 2026-09-07 auf kai-pi5. ``pi_apply_systemd_units.sh`` meldete fuer
alle fuenf Units

    beweis: kai-server.service byte-gleich
    OK: 5 Unit(s) angewendet und bewiesen.

und gleichzeitig lief kai-server mit dem Interpreter aus dem CHECKOUT-venv und
schrieb keinen Prozessmarker. Die Ursache lag nicht in der Unit-Datei, sondern
daneben: ``/etc/systemd/system/kai-server.service.d/graceful-shutdown.conf``
(root, 23.06.) setzte ``ExecStart=`` zurueck und danach neu. systemd wertet
Drop-Ins NACH der Unit aus; ein zweites ``ExecStart=`` ersetzt den Befehl
vollstaendig. Der Beweis war korrekt und die Zusage trotzdem falsch, weil er
eine Datei verglich, die nicht allein entscheidet.

Der Test faehrt deshalb das ECHTE Skript in einer Sandbox
(``PI_UNIT_APPLY_SRC/DST/SUDO``, gestubbtes ``systemctl``) und liest den
Exit-Code. Dass die Pruefung im Quelltext steht, ist keine Zusage --
TEXT_PRESENT != EXECUTABLE_BEHAVIOR.

GEGENPROBE (PROOF_INVERSION). Der Vorwurf an so einen Test lautet: er faellt
womoeglich nur an, weil in der Sandbox ohnehin etwas nicht stimmt. Deshalb
variiert ``test_ohne_dropin_bleibt_der_lauf_gruen`` genau die Kulisse, auf die
es ankommt -- dieselbe Sandbox, derselbe Aufruf, nur ohne Drop-In -- und
verlangt Gruen. Und ``test_harmloses_dropin_ist_kein_befund`` haelt das Drop-In
da, entfernt aber die eine Direktive, die ueber den geladenen Code entscheidet.
Faellt der Lauf auch dort rot aus, misst der Test die Sandbox und nicht den
Vertrag.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NL = chr(10)

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pi_apply_systemd_units.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="Shell-Skript-Vertrag (laeuft in CI auf Linux)",
)

UNIT = "kai-dropin-probe.service"

#: Bewusst NICHT release-gebunden (kein WorkingDirectory=.../current): sonst
#: ueberspringt der Release-Guard die Unit und der Lauf endet mit 10, bevor die
#: Drop-In-Pruefung ueberhaupt drankommt. Der Test soll den Drop-In-Vertrag
#: messen, nicht den Guard.
UNIT_TEXT = NL.join(
    [
        "[Unit]",
        "Description=Drop-In-Vertragsprobe",
        "",
        "[Service]",
        "Type=oneshot",
        "ExecStart=/bin/true",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
)

#: Der Nachbau des echten Vorfalls: ``ExecStart=`` leer, danach ein anderer
#: Befehl. Genau diese zwei Zeilen liessen kai-server aus dem Checkout starten.
DROPIN_GEFAEHRLICH = NL.join(
    [
        "[Service]",
        "ExecStart=",
        "ExecStart=/home/ubuntu/ai_analyst_trading_bot/.venv/bin/python3 -m app.api.main",
        "",
    ]
)

#: Aendert das Abschaltverhalten, nicht den geladenen Code. Solche Drop-Ins
#: liegen real auf der Pi (``stop-timeout.conf``). Sie zu melden hiesse, jedem
#: Deploy einen Dauerbefund mitzugeben -- und Dauerbefunde werden weggeschaut.
DROPIN_HARMLOS = NL.join(["[Service]", "TimeoutStopSec=90", "", ""])


def _sandbox(tmp: Path, *, dropin: str | None, abweichend: bool = False) -> dict[str, str]:
    """Standardmaessig sind Quelle und Ziel byte-gleich.

    Der Byte-Beweis ist damit GRUEN -- die Lage, in der der Drop-In-Fund
    ueberhaupt etwas Neues aussagt. ``abweichend=True`` erzeugt echten Drift,
    damit sich pruefen laesst, ob ein Lauf wirklich anwendet (und wann nicht).
    """
    src = tmp / "deploy" / "systemd"
    dst = tmp / "etc"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    (src / UNIT).write_text(UNIT_TEXT, encoding="utf-8", newline="")
    (dst / UNIT).write_text(
        UNIT_TEXT.replace("/bin/true", "/bin/false") if abweichend else UNIT_TEXT,
        encoding="utf-8",
        newline="",
    )

    if dropin is not None:
        d = dst / (UNIT + ".d")
        d.mkdir()
        (d / "graceful-shutdown.conf").write_text(dropin, encoding="utf-8", newline="")

    stub = tmp / "systemctl"
    stub.write_text(NL.join(["#!/usr/bin/env bash", "exit 0", ""]), encoding="utf-8", newline="")
    stub.chmod(0o755)

    return {
        "PI_UNIT_APPLY_SRC": str(src),
        "PI_UNIT_APPLY_DST": str(dst),
        "PI_UNIT_APPLY_SUDO": "",
        "PI_UNIT_SYNC_SYSTEMCTL": str(stub),
        "KAI_UNIT_BACKUP_DIR": str(tmp / "backups"),
    }


def _lauf(tmp: Path, umgebung: dict[str, str]) -> subprocess.CompletedProcess[str]:
    import os

    env = {**os.environ, **umgebung}
    return subprocess.run(
        ["bash", str(SKRIPT), "--yes"],
        cwd=str(tmp),
        env=env,
        capture_output=True,
        text=True,
    )


def test_dropin_ueber_execstart_beendet_den_lauf_rot(tmp_path: Path) -> None:
    """Die eigentliche Zusage: der Byte-Beweis allein macht nicht mehr gruen."""
    env = _sandbox(tmp_path, dropin=DROPIN_GEFAEHRLICH)
    p = _lauf(tmp_path, env)

    assert p.returncode == 4, (
        f"rc={p.returncode} -- ein Drop-In setzt ExecStart neu, der Lauf haette "
        f"das melden muessen.{NL}stdout={p.stdout}{NL}stderr={p.stderr}"
    )
    assert "DROPIN_OVERRIDE" in p.stderr
    assert "ExecStart" in p.stderr, "der Operator muss erfahren, WELCHE Direktive"
    assert "graceful-shutdown.conf" in p.stderr, "und in WELCHER Datei"


def test_bei_einem_fund_wird_nichts_angefasst(tmp_path: Path) -> None:
    """Der Fund haelt den Lauf an, bevor die erste Datei geschrieben wird.

    Hier weicht das Ziel wirklich ab -- ohne diese Divergenz waere "unveraendert"
    trivial wahr und der Test wertlos. Ein Rollback waere ohnehin die falsche
    Abhilfe: falsch ist das Drop-In, nicht die Unit. Also gar nicht erst
    anfassen; es entsteht kein halber Zustand.
    """
    env = _sandbox(tmp_path, dropin=DROPIN_GEFAEHRLICH, abweichend=True)
    ziel = Path(env["PI_UNIT_APPLY_DST"]) / UNIT
    vorher = ziel.read_text(encoding="utf-8")

    p = _lauf(tmp_path, env)

    assert p.returncode == 4, f"rc={p.returncode}{NL}{p.stderr}"
    assert ziel.read_text(encoding="utf-8") == vorher, (
        "trotz Fund wurde angewendet -- der Lauf haette vorher anhalten muessen"
    )


def test_ohne_fund_wird_dieselbe_abweichung_angewendet(tmp_path: Path) -> None:
    """Gegenprobe zum vorigen Test: sonst hiesse "unveraendert" nur "tut nie was".

    Gleiche Divergenz, gleicher Aufruf, nur ohne Drop-In -- und jetzt MUSS die
    Datei geschrieben werden.
    """
    env = _sandbox(tmp_path, dropin=None, abweichend=True)
    ziel = Path(env["PI_UNIT_APPLY_DST"]) / UNIT

    p = _lauf(tmp_path, env)

    assert p.returncode == 0, f"rc={p.returncode}{NL}{p.stdout}{NL}{p.stderr}"
    assert ziel.read_text(encoding="utf-8") == UNIT_TEXT, (
        "das Skript wendet in dieser Sandbox gar nichts an -- dann beweist der Test darueber nichts"
    )


def test_ohne_dropin_bleibt_der_lauf_gruen(tmp_path: Path) -> None:
    """Gegenprobe 1: dieselbe Sandbox ohne Drop-In muss durchlaufen.

    Ohne sie koennte der rote Lauf oben jede beliebige Ursache haben.
    """
    env = _sandbox(tmp_path, dropin=None)
    p = _lauf(tmp_path, env)

    assert p.returncode == 0, (
        f"rc={p.returncode} -- die Sandbox selbst ist schon rot, der Befund oben "
        f"beweist dann nichts.{NL}stdout={p.stdout}{NL}stderr={p.stderr}"
    )
    assert "DROPIN_OVERRIDE" not in p.stderr


def test_harmloses_dropin_ist_kein_befund(tmp_path: Path) -> None:
    """Gegenprobe 2: Drop-In vorhanden, aber ohne Einfluss auf den Code.

    Variiert genau eine Bedingung gegenueber dem roten Lauf -- die Direktive.
    Waere auch das rot, meldete jeder Deploy auf der echten Pi einen Befund
    wegen ``stop-timeout.conf``.
    """
    env = _sandbox(tmp_path, dropin=DROPIN_HARMLOS)
    p = _lauf(tmp_path, env)

    assert p.returncode == 0, (
        f"rc={p.returncode} -- TimeoutStopSec entscheidet nicht, welcher Code "
        f"laeuft.{NL}stdout={p.stdout}{NL}stderr={p.stderr}"
    )
    assert "DROPIN_OVERRIDE" not in p.stderr
