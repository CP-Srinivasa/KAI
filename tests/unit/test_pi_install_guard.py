r"""Der Installer darf provisionieren — aber nicht unbemerkt deployen.

Befund 2026-08-21 (Operator, am Code bestaetigt): ``pi_install_systemd.sh``
kopiert in einer Schleife JEDE Unit per ``install -m 0644`` nach
``/etc/systemd/system``, und zwar VOR dem Broker-Block. Wer das Skript benutzt,
um den Privilegien-Broker zu installieren, wendet damit als Nebenwirkung 24
divergente Unit-Dateien an:

* ohne Sicherung      — kein Rueckweg
* ohne Freeze-Guard   — ein eingefrorener Writer wird ueberschrieben
* ohne Beweise        — kein ``cmp``, kein ``NextElapse``-Check
* ohne Rollback       — halb angewendet bleibt halb angewendet

Genau dafuer existiert ``scripts/pi_apply_systemd_units.sh``. Der Installer
haette ihn stillschweigend umgangen — und das ausgerechnet bei dem Schritt, der
den P0-Zustand aufloesen soll.

Die Unterscheidung, die diese Tests halten, ist NICHT "Installer boese":

    frischer Host     -> im Ziel fehlt die Unit oder ist deckungsgleich
                         => Massenkopie ist genau richtig
    laufendes System  -> im Ziel liegt eine ABWEICHENDE Unit
                         => das ist eine Live-Aenderung, kein Provisionieren
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "scripts" / "lib" / "pi_install_guard.sh"
INSTALLER = REPO / "scripts" / "pi_install_systemd.sh"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")

_OLD = "OnBootSec=5min\n"
_NEW = "OnActiveSec=5min\n"


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run(  # noqa: S603
        [_BASH, "-c", script], capture_output=True, text=True, check=False
    )


def _dirs(tmp_path: Path, src_files: dict[str, str], dst_files: dict[str, str]):
    src = tmp_path / "deploy"
    dst = tmp_path / "etc"
    src.mkdir()
    dst.mkdir()
    for name, body in src_files.items():
        (src / name).write_text(body, encoding="utf-8")
    for name, body in dst_files.items():
        (dst / name).write_text(body, encoding="utf-8")
    return src, dst


def _drift(src: Path, dst: Path) -> tuple[str, int]:
    proc = _bash(
        f'set -uo pipefail; . "{GUARD.as_posix()}"; '
        f'pi_install_units_drift "{src.as_posix()}" "{dst.as_posix()}"; '
        f'pi_install_units_allowed "{src.as_posix()}" "{dst.as_posix()}"; echo "RC=$?"'
    )
    return proc.stdout, proc.returncode


def _allowed(src: Path, dst: Path) -> int:
    proc = _bash(
        f'set -uo pipefail; . "{GUARD.as_posix()}"; '
        f'pi_install_units_allowed "{src.as_posix()}" "{dst.as_posix()}"'
    )
    return proc.returncode


# ── Provisionierung bleibt erlaubt ──────────────────────────────────────────


def test_a_fresh_host_may_be_provisioned(tmp_path: Path) -> None:
    """Gegenprobe zuerst: ein leeres Ziel ist kein Drift.

    Ohne diesen Test waere das Gate nur eine Mauer — und der Installer haette
    seinen eigentlichen Zweck verloren.
    """
    src, dst = _dirs(tmp_path, {"kai-x.timer": _NEW, "kai-y.service": _NEW}, {})

    assert _allowed(src, dst) == 0


def test_an_identical_target_is_not_drift(tmp_path: Path) -> None:
    """Ein erneuter Installerlauf auf gleichem Stand darf nicht blockieren."""
    src, dst = _dirs(tmp_path, {"kai-x.timer": _NEW}, {"kai-x.timer": _NEW})

    assert _allowed(src, dst) == 0


def test_a_missing_unit_is_provisioning_not_overwriting(tmp_path: Path) -> None:
    """Neu anlegen heisst: es gibt nichts zu sichern und nichts zu verlieren."""
    src, dst = _dirs(tmp_path, {"kai-x.timer": _NEW, "kai-new.timer": _NEW}, {"kai-x.timer": _NEW})

    assert _allowed(src, dst) == 0


# ── Live-Aenderung wird abgelehnt ───────────────────────────────────────────


def test_a_differing_unit_blocks_the_bulk_copy(tmp_path: Path) -> None:
    """Der Realfall: 24 divergente Units auf einem laufenden System."""
    src, dst = _dirs(tmp_path, {"kai-x.timer": _NEW}, {"kai-x.timer": _OLD})

    assert _allowed(src, dst) == 10


def test_drift_names_the_offending_units(tmp_path: Path) -> None:
    """Der Operator soll nicht raten muessen, WAS abweicht."""
    src, dst = _dirs(
        tmp_path,
        {"kai-a.timer": _NEW, "kai-b.timer": _NEW, "kai-c.service": _NEW},
        {"kai-a.timer": _OLD, "kai-b.timer": _NEW, "kai-c.service": _OLD},
    )

    out, _ = _drift(src, dst)

    assert "kai-a.timer" in out
    assert "kai-c.service" in out
    assert "kai-b.timer" not in out.replace("RC=", "")


def test_non_unit_files_are_ignored(tmp_path: Path) -> None:
    """``.bak``-Sicherungen aus Handeingriffen sind kein Drift — systemd liest sie nie."""
    src, dst = _dirs(tmp_path, {"kai-x.timer": _NEW}, {"kai-x.timer": _NEW})
    (dst / "kai-x.timer.bak-20260817").write_text(_OLD, encoding="utf-8")
    (src / "README.md").write_text("nicht relevant\n", encoding="utf-8")

    assert _allowed(src, dst) == 0


def test_the_refusal_names_the_safe_path(tmp_path: Path) -> None:
    """Ein Gate, das kein Mittel nennt, ist nur eine Blockade."""
    proc = _bash(f'set -uo pipefail; . "{GUARD.as_posix()}"; pi_install_units_refusal 24')

    assert "pi_apply_systemd_units.sh" in proc.stderr
    assert "--broker-only" in proc.stderr
    assert "--force-units" in proc.stderr


# ── Der Installer selbst ────────────────────────────────────────────────────


def _installer_source() -> str:
    """Kommentare strippen — der Kopf ERKLAERT den Befund und wuerde sonst treffen."""
    lines = INSTALLER.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _install_body() -> str:
    """Nur der Rumpf von ``install()``.

    Ohne diese Eingrenzung trifft die Suche die Schleife in ``uninstall()`` —
    der erste Entwurf dieses Tests tat genau das und meldete einen Fehler, den
    es nicht gab.
    """
    code = _installer_source()
    start = code.index("\ninstall() {")
    return code[start : code.index("\n}", start)]


def test_the_bulk_copy_sits_behind_the_gate() -> None:
    """Gate VOR der Kopierschleife — an der Stelle, auf die es ankommt."""
    body = _install_body()

    gate = body.index("pi_install_units_allowed")
    loop = body.index('for unit in "${UNITS[@]}"')

    assert gate < loop, "das Gate muss vor der Kopierschleife stehen"


def test_each_installer_function_is_defined_exactly_once() -> None:
    """Regressionswache gegen einen Fehler, den ich beim Umbau selbst gemacht habe.

    Ein Einfuegen am Anker ``install() {`` traf ``uninstall() {`` MITTEN im
    Namen: ``uninstall`` verschwand, ``install`` war doppelt definiert, und
    ``bash -n`` blieb still, weil das syntaktisch gueltig ist. Nur ein Test, der
    die Definitionen ZAEHLT, faengt so etwas.
    """
    code = _installer_source()

    for name in ("install", "uninstall", "install_broker", "broker_only", "reactivate_only"):
        assert code.count(f"\n{name}() {{") == 1, f"{name}() ist nicht genau einmal definiert"


def test_broker_only_touches_no_unit() -> None:
    """Der Pfad, den der P0-Schritt braucht: kleinste Angriffsflaeche."""
    code = _installer_source()

    start = code.index("broker_only() {")
    body = code[start : code.index("\n}", start)]

    assert "install_broker" in body
    assert 'for unit in "${UNITS[@]}"' not in body
    assert "daemon-reload" not in body
    assert "systemctl enable" not in body


def test_broker_only_short_circuits_the_dispatch() -> None:
    """``--broker-only`` muss VOR jedem anderen Pfad greifen und dort enden.

    Der erste Entwurf dieses Tests war vakuum-gruen: eine Ternary-Konstruktion
    machte die Assertion bedingungslos wahr. Ein Test, der nicht rot werden
    kann, ist keiner.
    """
    code = _installer_source()
    dispatch = code[code.index('if [[ "${BASH_SOURCE[0]}" == "${0}" ]]') :]

    assert dispatch.index("BROKER_ONLY == 1") < dispatch.index("REACTIVATE_ONLY == 1")
    assert dispatch.index("BROKER_ONLY == 1") < dispatch.index("UNINSTALL == 1")
    assert dispatch.index("BROKER_ONLY == 1") < dispatch.index("\n        install\n")
    assert "broker_only\n        exit" in dispatch, "der Pfad muss dort enden"


def test_the_flag_is_actually_parsed() -> None:
    """Sonst waere ``--broker-only`` ein ``unknown arg`` und der Operator staende ohne Weg da."""
    code = _installer_source()

    assert "--broker-only) BROKER_ONLY=1" in code
    assert "--force-units) FORCE_UNITS=1" in code


def test_broker_install_still_proves_its_postconditions() -> None:
    """Die Beweise aus #739 duerfen beim Herausloesen nicht verloren gehen."""
    code = _installer_source()

    start = code.index("install_broker() {")
    body = code[start : code.index("\nbroker_only", start)]

    assert "root:root:755" in body
    assert "cmp -s" in body
    assert "-o root -g root" in body


def test_installer_is_syntactically_valid() -> None:
    for path in (INSTALLER, GUARD):
        proc = _bash(f'bash -n "{path.as_posix()}"')
        assert proc.returncode == 0, f"{path.name}: {proc.stderr}"
