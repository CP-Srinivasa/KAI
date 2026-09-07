"""Ein relativer Zustandspfad darf ein aktives Release nicht unsichtbar machen.

Gemessen am 2026-09-07 auf kai-pi5: alle fuenf Prozessmarker zeigten korrekt auf
das aktive Release, ``current`` stand, der Deploy-Marker stimmte, ``/health``
meldete den richtigen Commit -- und trotzdem rief die Sonde alle 15 Minuten

    [CRITICAL] runtime_provenance: runtime-provenance: HOLD -- 0 von 5 Diensten
    repo-basiert ... DEPENDENCY-DRIFT Marker wurde fuer repo_sha 9293c423
    geschrieben

Der ``dependency_marker`` gehoert zum reinen Checkout-Modell, und das Skript,
das ihn schrieb, ist auf der Mainline geloescht. Im Release-Modell kann ihn
also niemand mehr aktualisieren: ein CRITICAL, das kein Operator je haette
schliessen koennen.

Die Ursache lag eine Ebene tiefer. ``app/alerts/health_check.py`` ruft mit dem
CWD-relativen Repo-Wurzelpfad auf. ``Path(".").parent`` ist wieder ``.``, also
suchte die Sonde ``./current`` IM Checkout statt ``../current`` daneben, fand
nichts, und ``release_governs`` meldete ``False`` -- woraufhin die stillgelegte
Checkout-Achse wieder ansprang.

``app/core/runtime_identity.py`` loest seit jeher auf, bevor es ``.parent``
nimmt; deshalb war ``/health`` gruen, waehrend die Sonde HOLD rief. Zwei
Ableitungen desselben Pfades, eine davon falsch.

Diese Datei prueft die Eigenschaft, auf die es ankommt: das Urteil haengt am
Zustand, nicht an der Schreibweise des Pfades.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.alerts.process_runtime_probe import (
    _active_release,
    checkout_axis_active,
    release_governs,
    release_provenance_problems,
)


def _symlinks_moeglich(tmp: Path) -> bool:
    """Windows verweigert ``os.symlink`` ohne Sonderrecht (WinError 1314).

    Der Vertrag hier IST der Symlink ``current`` -- ohne ihn gibt es nichts zu
    pruefen. Auf dem Ziel-System (Linux/Pi) laeuft der Test, und dort ist er
    aussagekraeftig; ihn per Mock nachzubauen hiesse, die eine Eigenschaft
    wegzumocken, um die es geht.
    """
    ziel = tmp / "_probe_ziel"
    ziel.mkdir()
    try:
        (tmp / "_probe_link").symlink_to(ziel, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


SHA = "c" * 40


@contextmanager
def _im_verzeichnis(pfad: Path) -> Iterator[None]:
    vorher = Path.cwd()
    os.chdir(pfad)
    try:
        yield
    finally:
        os.chdir(vorher)


def _welt(tmp: Path) -> Path:
    """Checkout, Release und ``current`` so, wie sie auf der Pi liegen."""
    if not _symlinks_moeglich(tmp):
        pytest.skip("Symlinks nicht erlaubt (Windows ohne Recht) — der Vertrag IST der Symlink")
    state = tmp / "ai_analyst_trading_bot"
    (state / "artifacts" / "runtime").mkdir(parents=True)

    release = tmp / "releases" / SHA
    (release / "app").mkdir(parents=True)
    (release / "app" / "main.py").write_text("x\n", encoding="utf-8")
    lock = release / "requirements.lock"
    lock.write_text("pkg==1.0\n", encoding="utf-8")
    # Der Lock-Hash wird GERECHNET, nicht erfunden: mit einem Phantasiewert
    # meldet die Kette RELEASE_LOCK_HASH_MISMATCH, beide Pfade sind dann
    # gleich kaputt -- und der Gleichheits-Test bewiese nichts.
    lock_sha = hashlib.sha256(lock.read_bytes()).hexdigest()

    from app.observability.release_identity import release_tree_sha256

    baum = release_tree_sha256(release)
    (release / "release.json").write_text(
        json.dumps(
            {
                "schema": "kai_release/v1",
                "repo_sha": SHA,
                "release_path": str(release),
                "release_tree_sha256": baum,
                "requirements_lock_sha256": lock_sha,
                "python_version": "3.12.0",
                "created_at_utc": "2026-09-07T00:00:00+00:00",
                "venv_python_path": str(release / ".venv" / "bin" / "python3"),
                # BEWUSST OHNE dependency_manifest_sha256: diese Fixture legt
                # keinen venv an, und seit `verify_release` das Manifest gegen
                # den venv haelt, waere das Feld hier eine Behauptung ohne
                # Deckung -- RELEASE_VENV_UNUSABLE, und die Release-Achse fiele
                # aus, obwohl dieser Test von Pfaden handelt und nicht von
                # Abhaengigkeiten. Ein Feld, das man nicht einloest, gehoert
                # nicht in eine Fixture.
                "builder_version": "test/1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # Der Baum-Hash deckt release.json nicht, also bleibt er nach dem Schreiben gueltig.
    (tmp / "current").symlink_to(release, target_is_directory=True)
    (state / "artifacts" / "runtime" / "deployment_marker.json").write_text(
        json.dumps(
            {
                "schema": "deployment_marker/v1",
                "repo_sha": SHA,
                "release_path": str(release),
                "release_tree_sha256": baum,
                "requirements_lock_sha256": lock_sha,
                "deployed_at_utc": "2026-09-07T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return state


def test_absoluter_und_relativer_pfad_urteilen_gleich(tmp_path: Path) -> None:
    """Die eigentliche Aussage: die Schreibweise darf nichts entscheiden."""
    state = _welt(tmp_path)

    absolut = release_provenance_problems(state)
    with _im_verzeichnis(state):
        relativ = release_provenance_problems(Path("."))

    assert absolut == relativ, (
        f"relativ={relativ} vs absolut={absolut} — der Zustand ist derselbe, "
        f"nur der Pfad ist anders geschrieben"
    )


def test_relativer_pfad_findet_das_aktive_release(tmp_path: Path) -> None:
    state = _welt(tmp_path)
    with _im_verzeichnis(state):
        pfad, baum = _active_release(Path("."))
    assert pfad, "current wurde ueber den relativen Pfad nicht gefunden"
    assert baum, "release_tree_sha256 fehlt — der Baum traegt seinen Anspruch nicht"


def test_relativer_pfad_legt_die_checkout_achse_still(tmp_path: Path) -> None:
    """Der Dauerbefund entstand genau hier: Achse an, obwohl das Release regiert."""
    state = _welt(tmp_path)
    with _im_verzeichnis(state):
        assert release_governs(Path(".")) is True
        assert checkout_axis_active(Path(".")) is False, (
            "die stillgelegte Checkout-Achse ist wieder aktiv — sie fordert einen "
            "dependency_marker, den im Release-Modell niemand mehr schreibt"
        )


def test_ohne_release_bleibt_die_checkout_achse_zustaendig(tmp_path: Path) -> None:
    """Die Gegenprobe: der Fix darf die Achse nicht generell abschalten."""
    state = tmp_path / "ai_analyst_trading_bot"
    (state / "artifacts" / "runtime").mkdir(parents=True)

    with _im_verzeichnis(state):
        assert release_provenance_problems(Path(".")) == ["RELEASE_NOT_ACTIVE"]
        assert release_governs(Path(".")) is False
        assert checkout_axis_active(Path(".")) is True
