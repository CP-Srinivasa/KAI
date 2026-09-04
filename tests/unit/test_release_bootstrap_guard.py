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
# Das Kriterium lebt seit dem Vorfall vom 2026-09-04 in einer Bibliothek, die
# Installer UND Unit-Sync teilen: zwei Implementierungen desselben Guards waeren
# zwei Wahrheiten.
RELEASE_GUARD = REPO_ROOT / "scripts" / "lib" / "pi_release_guard.sh"
UNIT_SYNC = REPO_ROOT / "scripts" / "lib" / "pi_unit_sync.sh"
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
    return (
        f'. "{RELEASE_GUARD.as_posix()}"\nUNIT_SRC="{units_dir.as_posix()}"\n'
        + "\n".join(block)
        + f"\n{unit}\n"
    )


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


def _gueltiges_release(ziel: Path) -> None:
    """Was `pi_make_release.sh` hinterlaesst: release.json UND den eigenen venv."""
    (ziel / ".venv" / "bin").mkdir(parents=True)
    (ziel / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (ziel / "release.json").write_text(json.dumps({"schema": "kai_release/v1"}), encoding="utf-8")


def test_ohne_venv_python_bricht_der_installer_ab(tmp_path: Path) -> None:
    """release.json ohne venv: ExecStart zeigt auf ein Binary, das es nicht gibt (203/EXEC)."""
    ziel = tmp_path / "releases" / "abc"
    ziel.mkdir(parents=True)
    (ziel / "release.json").write_text(json.dumps({"schema": "kai_release/v1"}), encoding="utf-8")
    units = _unit_mit_ziel(tmp_path, ziel)
    r = _run(_helpers(units, unit="assert_release_ready"))
    assert r.returncode != 0
    assert "ohne .venv/bin/python" in r.stderr


def test_mit_gueltigem_release_laeuft_der_installer_durch(tmp_path: Path) -> None:
    """Positivkontrolle — sonst wuerde der Guard einfach immer abbrechen."""
    ziel = tmp_path / "releases" / "abc"
    _gueltiges_release(ziel)
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
    """Reihenfolge im ABLAUF: pruefen, dann starten.

    Verglichen werden die AUFRUFE, nicht beliebige Textstellen — die Definition
    von ``enable_on_install_units`` steht naturgemaess vor ihrem Aufruf, ein
    reiner Textpositionsvergleich waere also von der Dateistruktur abhaengig
    statt vom Ablauf.

    Der Guard sitzt bewusst im Ablauf und nicht IN ``enable_on_install_units``:
    diese Funktion hat einen eigenen, getesteten Vertrag (Freeze-Marker), und
    eine zweite Bedingung darin liess ihre Tests an einer Umgebung scheitern,
    ueber die sie gar nichts aussagen.
    """
    src = INSTALLER.read_text(encoding="utf-8")
    guard = src.index("if ! assert_release_ready; then")
    enable = src.index("if ! enable_on_install_units; then")
    assert guard < enable, "der Guard muss VOR dem Enable-Aufruf stehen"
    # Und er darf nicht in die Enable-Funktion zurueckwandern.
    body_start = src.index("enable_on_install_units() {")
    body_end = src.index("reactivate_critical() {")
    assert "assert_release_ready" not in src[body_start:body_end]


# ── Die gemeinsame Bibliothek (Vorfall 2026-09-04) ──────────────────────────
#
# `pi_apply_systemd_units.sh` hat die fuenf Units nach /etc kopiert, obwohl es
# `/home/kai/current` nicht gab; der naechste Restart scheiterte mit 200/CHDIR.
# Der Guard hier deckte nur `enable --now` ab. Seitdem teilen Installer und
# Unit-Sync EIN Kriterium in `scripts/lib/pi_release_guard.sh`.


def _lib(script: str) -> subprocess.CompletedProcess[str]:
    return _run(f'. "{RELEASE_GUARD.as_posix()}"\n{script}\n')


def test_installer_und_unit_sync_teilen_den_guard() -> None:
    assert RELEASE_GUARD.exists()
    assert "lib/pi_release_guard.sh" in INSTALLER.read_text(encoding="utf-8")
    assert "pi_release_guard.sh" in UNIT_SYNC.read_text(encoding="utf-8")
    # Sourcen darf nichts tun.
    r = _lib("echo SOURCED_OK")
    assert r.stdout.strip() == "SOURCED_OK" and r.returncode == 0, r.stderr


def test_das_ziel_kommt_aus_repo(tmp_path: Path) -> None:
    units = _unit_mit_ziel(tmp_path, tmp_path / "current")
    r = _lib(f'pi_release_unit_target "{(units / "kai-test.service").as_posix()}"')
    assert r.stdout.strip() == str(tmp_path / "current")


def test_working_directory_auf_current_zaehlt_auch_ohne_runtime_exec(tmp_path: Path) -> None:
    """Der Vorfall war ein CHDIR — WorkingDirectory allein reicht fuer die Bindung.

    Eine kuenftige Release-Unit ohne `runtime-exec` (etwa ein Oneshot, der ins
    Release umzieht) faellt sonst durch dasselbe Loch wie am 04.09.
    """
    f = tmp_path / "kai-oneshot.service"
    f.write_text(
        f"[Service]\nWorkingDirectory={(tmp_path / 'current').as_posix()}\n"
        "ExecStart=/x/python -m app.cli.main pipeline run\n",
        encoding="utf-8",
    )
    r = _lib(f'pi_release_unit_target "{f.as_posix()}"')
    assert r.stdout.strip() == (tmp_path / "current").as_posix()


def test_checkout_unit_hat_kein_release_ziel(tmp_path: Path) -> None:
    f = tmp_path / "kai-alt.service"
    f.write_text(
        "[Service]\nWorkingDirectory=/home/kai/ai_analyst_trading_bot\n"
        "ExecStart=/x/python -m app.cli.main pipeline run\n",
        encoding="utf-8",
    )
    r = _lib(f'pi_release_unit_target "{f.as_posix()}"; echo "RC=$?"')
    assert r.stdout.strip() == "RC=0", "leer, aber kein Fehler — die Unit ist nur nicht gebunden"


def test_der_grund_wird_benannt(tmp_path: Path) -> None:
    """Ein Guard, der nur `1` sagt, schickt den Operator raten."""
    fehlt = tmp_path / "gibtsnicht"
    r = _lib(f'pi_release_active_reason "{fehlt.as_posix()}"; echo "RC=$?"')
    assert "existiert nicht" in r.stdout and "RC=1" in r.stdout
    ziel = tmp_path / "current"
    ziel.mkdir()
    r = _lib(f'pi_release_active_reason "{ziel.as_posix()}"; echo "RC=$?"')
    assert "ohne release.json" in r.stdout and "RC=1" in r.stdout
    _gueltiges_release(ziel)
    r = _lib(f'pi_release_active_reason "{ziel.as_posix()}"; echo "RC=$?"')
    assert r.stdout.strip() == "RC=0", r.stdout
