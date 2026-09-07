"""Was nach dem Bau in den venv kommt, faellt jetzt auf.

Bis 2026-09-07 sah das NICHTS. ``release_tree_sha256`` schliesst ``.venv``
ausdruecklich aus (``EXCLUDED_NAMES``), begruendet damit, dass das Lockfile ihn
belege. Diese Begruendung hat eine Voraussetzung, die nirgends erzwungen wird:
sie gilt nur, solange ausschliesslich aus dem Lockfile installiert wird. Wer
danach etwas von Hand hineinlegt, aendert den ausgelieferten Code, ohne ein
einziges Identitaetsfeld zu bewegen -- ``verify_release`` blieb gruen,
``pip check`` blieb gruen, der Deploy-Marker blieb gruen.

Nach der Beweisklasse PROOF_INVERSION ist das der P0-Fall: eine Pruefung
besteht, aber nicht wegen der behaupteten Eigenschaft. Deshalb steht hier nicht
nur "gleiches Manifest -> gruen", sondern vor allem die Gegenprobe: ein
zusaetzliches Paket MUSS rot werden.

Die ``freeze``-Naht macht das ohne 500-MB-venv pruefbar. Die Rechnung selbst ist
dieselbe, die ``pi_make_release.sh`` beim Bau aufruft -- zwei Implementierungen
desselben Hashes waeren zwei Wahrheiten, und genau dieser Satz steht seit jeher
ueber dem Baum-Hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.observability.release_identity import (
    PROBLEM_DEPENDENCY_DRIFT,
    PROBLEM_VENV_UNUSABLE,
    dependency_manifest_sha256,
    release_tree_sha256,
    verify_release,
)

SHA = "e" * 40
GEBAUT = ("httpx==0.27.0", "pydantic==2.7.1", "uvicorn==0.30.1")
NACHTRAEGLICH = (*GEBAUT, "litellm==1.99.0")


def _freeze(zeilen: tuple[str, ...]):
    def _fn(_root: Path) -> str:
        return "\n".join(zeilen) + "\n"

    return _fn


def _release(tmp: Path, *, aufgezeichnet: str | None, venv: bool = True) -> Path:
    root = tmp / "releases" / SHA
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("x\n", encoding="utf-8")
    (root / "requirements.lock").write_text("pkg==1.0\n", encoding="utf-8")
    if venv:
        (root / ".venv" / "bin").mkdir(parents=True)
        python = root / ".venv" / "bin" / "python3"
        python.write_text("#!/bin/sh\n", encoding="utf-8")

    daten = {
        "schema": "kai_release/v1",
        "repo_sha": SHA,
        "release_path": str(root),
        "release_tree_sha256": release_tree_sha256(root),
        "requirements_lock_sha256": hashlib.sha256(
            (root / "requirements.lock").read_bytes()
        ).hexdigest(),
        "python_version": "3.12.0",
        "created_at_utc": "2026-09-07T00:00:00+00:00",
        "venv_python_path": str(root / ".venv" / "bin" / "python3"),
        "builder_version": "test/1",
    }
    if aufgezeichnet is not None:
        daten["dependency_manifest_sha256"] = aufgezeichnet
    (root / "release.json").write_text(json.dumps(daten) + "\n", encoding="utf-8")
    return root


def test_unveraenderter_venv_bleibt_gruen(tmp_path: Path) -> None:
    root = _release(tmp_path, aufgezeichnet=None)
    beim_bau = dependency_manifest_sha256(root, freeze=_freeze(GEBAUT))
    assert beim_bau
    root_mit = _release(tmp_path / "zweit", aufgezeichnet=beim_bau)

    assert verify_release(root_mit, freeze=_freeze(GEBAUT)) == []


def test_ein_nachtraeglich_installiertes_paket_wird_rot(tmp_path: Path) -> None:
    """DIE Gegenprobe: der Fall, den bis heute nichts gesehen haette."""
    root = _release(tmp_path, aufgezeichnet=None)
    beim_bau = dependency_manifest_sha256(root, freeze=_freeze(GEBAUT))
    root_mit = _release(tmp_path / "zweit", aufgezeichnet=beim_bau)

    probleme = verify_release(root_mit, freeze=_freeze(NACHTRAEGLICH))

    assert PROBLEM_DEPENDENCY_DRIFT in probleme, (
        "ein zusaetzliches Paket im venv blieb unbemerkt — genau die Luecke, "
        "gegen die diese Pruefung gebaut ist"
    )


def test_ein_entferntes_paket_wird_ebenfalls_rot(tmp_path: Path) -> None:
    """Drift hat zwei Richtungen; nur eine zu sehen waere die halbe Zusage."""
    root = _release(tmp_path, aufgezeichnet=None)
    beim_bau = dependency_manifest_sha256(root, freeze=_freeze(NACHTRAEGLICH))
    root_mit = _release(tmp_path / "zweit", aufgezeichnet=beim_bau)

    assert PROBLEM_DEPENDENCY_DRIFT in verify_release(root_mit, freeze=_freeze(GEBAUT))


def test_ein_release_ohne_das_feld_faellt_nicht_ruecklings_durch(tmp_path: Path) -> None:
    """Aeltere Releases kennen das Feld nicht — sie duerfen davon nicht sterben."""
    root = _release(tmp_path, aufgezeichnet=None)

    probleme = verify_release(root, freeze=_freeze(NACHTRAEGLICH))

    assert PROBLEM_DEPENDENCY_DRIFT not in probleme
    assert PROBLEM_VENV_UNUSABLE not in probleme


def test_fehlender_interpreter_ist_ein_eigener_befund(tmp_path: Path) -> None:
    """ "Nicht pruefbar" ist nicht dasselbe wie "in Ordnung"."""
    root = _release(tmp_path, aufgezeichnet="f" * 64, venv=False)

    probleme = verify_release(root)

    assert PROBLEM_VENV_UNUSABLE in probleme
    assert PROBLEM_DEPENDENCY_DRIFT not in probleme


def test_die_reihenfolge_der_pakete_aendert_den_hash_nicht(tmp_path: Path) -> None:
    """Sonst waere jeder zweite Lauf ein Fehlalarm — und ein Werkzeug, das
    staendig rot ist, wird nach drei Tagen ignoriert."""
    root = _release(tmp_path, aufgezeichnet=None)
    vorwaerts = dependency_manifest_sha256(root, freeze=_freeze(GEBAUT))
    rueckwaerts = dependency_manifest_sha256(root, freeze=_freeze(tuple(reversed(GEBAUT))))

    assert vorwaerts == rueckwaerts


def test_das_hashformat_ist_festgenagelt(tmp_path: Path) -> None:
    """Die Rechnung muss der Shell-Pipeline des Builders entsprechen.

    `pip freeze | LC_ALL=C sort | sha256sum` -- sortierte Zeilen, je ein
    abschliessender Zeilenumbruch. Auf kai-pi5 gegen ein echtes Release
    gemessen: beide liefern 03a00269176b89c7… fuer denselben venv.
    """
    root = _release(tmp_path, aufgezeichnet=None)
    erwartet = hashlib.sha256(("\n".join(sorted(GEBAUT)) + "\n").encode("utf-8")).hexdigest()

    assert dependency_manifest_sha256(root, freeze=_freeze(GEBAUT)) == erwartet
