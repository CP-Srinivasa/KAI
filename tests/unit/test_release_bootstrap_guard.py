"""Der Installer darf keine Unit in einen leeren Release-Pfad starten.

**Der Befund, gegen den diese Datei steht.** Die fuenf release-gebundenen Units
zeigen mit ``WorkingDirectory``, ``.venv``, ``--repo`` und dem Kommando nach
``--`` auf ``/home/kai/current``. Auf dem Pi existiert dieser Pfad heute nicht,
und ``pi_install_systemd.sh`` kannte weder ``releases/`` noch ``current`` — kein
einziger Treffer. Der Installer macht aber ``systemctl enable --now`` und
``restart``.

Waere nach dem Merge zuerst der Installer gefahren worden, haetten alle fuenf
Dauerlaeufer in einen nicht existierenden Pfad gestartet und sofort versagt —
bevor irgendeine Provenance-Sonde ueberhaupt etwas zu bewerten gehabt haette.
Das ist kein Provenance-Befund, das ist ein toter Dienst.

Die Unit-Menge kommt aus derselben selbstpflegenden Quelle wie
``expected_attesting_units``: wer ``runtime-exec`` fuehrt, ist release-gebunden.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "pi_install_systemd.sh"
UNIT_DIR = REPO_ROOT / "deploy" / "systemd"

_BASH = shutil.which("bash")


def _helpers(units_dir: Path, *, unit: str = "") -> str:
    """Die beiden Hilfsfunktionen des Installers isoliert ausfuehrbar machen."""
    src = INSTALLER.read_text(encoding="utf-8")
    block: list[str] = []
    keep = False
    for line in src.splitlines():
        if line.startswith(
            ("release_bound_units()", "release_target_of()", "assert_release_ready()")
        ):
            keep = True
        if keep:
            block.append(line)
        if keep and line == "}":
            keep = False
    return f'UNIT_SRC="{units_dir.as_posix()}"\n' + "\n".join(block) + f"\n{unit}\n"


def _run(script: str) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run(  # noqa: S603
        [_BASH, "-c", script], capture_output=True, text=True, timeout=60, check=False
    )


pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")


def test_die_release_gebundenen_units_werden_aus_der_quelle_gelesen(tmp_path: Path) -> None:
    """Keine handgefuehrte Liste — dieselbe Quelle wie expected_attesting_units."""
    r = _run(_helpers(UNIT_DIR, unit="release_bound_units"))
    gefunden = sorted(r.stdout.split())
    assert gefunden == [
        "kai-agent-worker.service",
        "kai-entry-watch.service",
        "kai-liquidation-stream.service",
        "kai-server.service",
        "kai-tg-listener.service",
    ], r.stdout


def test_das_release_ziel_wird_aus_der_unit_extrahiert() -> None:
    r = _run(_helpers(UNIT_DIR, unit="release_target_of kai-server.service"))
    assert r.stdout.strip() == "/home/kai/current"


def _unit_mit_ziel(tmp_path: Path, ziel: Path) -> Path:
    d = tmp_path / "units"
    d.mkdir(parents=True, exist_ok=True)
    (d / "kai-test.service").write_text(
        "[Service]\n"
        f"WorkingDirectory={ziel}\n"
        "ExecStart=/x/python -m app.cli.main trading runtime-exec "
        f"--unit %n --repo {ziel} -- /x/python -m app\n",
        encoding="utf-8",
    )
    return d


def test_ohne_current_bricht_der_installer_ab(tmp_path: Path) -> None:
    """Der eigentliche Schutz: kein Start in einen leeren Pfad."""
    units = _unit_mit_ziel(tmp_path, tmp_path / "gibtsnicht")
    r = _run(_helpers(units, unit="assert_release_ready"))
    assert r.returncode != 0, "der Installer haette in einen leeren Pfad gestartet"
    assert "existiert nicht" in r.stderr
    assert "pi_make_release" in r.stderr, "die Meldung muss den Ausweg nennen"


def test_ohne_release_json_bricht_der_installer_ab(tmp_path: Path) -> None:
    """Ein Verzeichnis allein ist kein Release."""
    ziel = tmp_path / "releases" / "abc"
    ziel.mkdir(parents=True)
    units = _unit_mit_ziel(tmp_path, ziel)
    r = _run(_helpers(units, unit="assert_release_ready"))
    assert r.returncode != 0
    assert "ohne release.json" in r.stderr


def test_mit_gueltigem_release_laeuft_der_installer_durch(tmp_path: Path) -> None:
    """Positivkontrolle — sonst wuerde der Guard einfach immer abbrechen."""
    ziel = tmp_path / "releases" / "abc"
    ziel.mkdir(parents=True)
    (ziel / "release.json").write_text(json.dumps({"schema": "kai_release/v1"}), encoding="utf-8")
    units = _unit_mit_ziel(tmp_path, ziel)
    r = _run(_helpers(units, unit="assert_release_ready"))
    assert r.returncode == 0, r.stderr


def test_units_ohne_release_bindung_werden_nicht_gefordert(tmp_path: Path) -> None:
    """Die uebrigen Units bleiben bewusst am alten Pfad und sind out of scope."""
    d = tmp_path / "units"
    d.mkdir(parents=True)
    (d / "kai-alt.service").write_text(
        "[Service]\nExecStart=/x/python -m app.cli.main pipeline run\n", encoding="utf-8"
    )
    r = _run(_helpers(d, unit="assert_release_ready"))
    assert r.returncode == 0


def test_der_installer_ruft_den_guard_vor_dem_enable() -> None:
    """Reihenfolge im Quelltext: pruefen, dann starten."""
    src = INSTALLER.read_text(encoding="utf-8")
    guard = src.index("assert_release_ready; then")
    enable = src.index('run systemctl enable --now "$unit"')
    assert guard < enable, "der Guard muss VOR dem enable stehen"
