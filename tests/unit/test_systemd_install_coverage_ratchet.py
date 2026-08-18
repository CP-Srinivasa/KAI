"""Ratchet: das Install-Skript darf nicht weiter hinter die Realität zurückfallen.

Befund 2026-08-17: ``scripts/pi_install_systemd.sh`` listet 54 Units, auf der
Platte liegen 113. **59 Units werden vom Install-Skript gar nicht kopiert** —
darunter aktiv laufende wie ``kai-technical-screener``, ``kai-truth-lint`` und
``kai-prereg-maturity``. Sie wurden von Hand installiert. Ein frischer Host
wäre also nur zur Hälfte funktionsfähig, und niemand würde es merken, solange
der bestehende Pi läuft (gleiche Familie wie der ``/home/kai``-Symlink).

Die 59 Altfälle werden hier eingefroren statt still gelassen — dieselbe Praxis
wie bei den 84 eingefrorenen Zerlegungs-Altfällen (#682/#684/#687). Der Test
lässt die Liste SCHRUMPFEN, aber nie wachsen: eine neu hinzugefügte Unit muss
ab sofort im Install-Skript stehen.

Besonders wichtig für ``kai-unit-failure-notify@.service``: zeigt ein
``OnFailure=`` auf eine Unit, die nie installiert wurde, läuft die
Fehlerbehandlung aller anderen Units ins Leere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _ROOT / "scripts" / "pi_install_systemd.sh"
_UNIT_DIR = _ROOT / "deploy" / "systemd"
_BASELINE = _ROOT / "tests" / "fixtures" / "systemd_install_gap_baseline.json"


def _listed_units() -> set[str]:
    """Was das Install-Skript tatsaechlich kopieren wuerde.

    Zwei Bauarten, beide muessen messbar bleiben — sonst ist der Ratchet nach
    dem Umbau blind statt streng:

    * **abgeleitet** (seit 2026-08-18): das Skript liest ``deploy/systemd/``
      selbst; dann ist die Kopierliste per Konstruktion die Platte.
    * **Handliste** (Altzustand): das ``UNITS=()``-Array wird geparst.
    """
    text = _INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r"^UNITS=\((.*?)^\)", text, re.M | re.S)
    if match is None:
        assert re.search(r"mapfile -t UNITS < <\(", text), (
            "Weder ein UNITS=()-Array noch eine abgeleitete Kopierliste gefunden — Ratchet blind."
        )
        return _units_on_disk()
    body = match.group(1)
    # Kommentarzeilen raus: ein erklaerender Kommentar darf keine Unit sein.
    effective = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))
    return set(re.findall(r'"([^"]+)"', effective))


def _units_on_disk() -> set[str]:
    return {p.name for p in _UNIT_DIR.glob("*.service")} | {
        p.name for p in _UNIT_DIR.glob("*.timer")
    }


def _baseline() -> set[str]:
    return set(json.loads(_BASELINE.read_text(encoding="utf-8"))["units"])


def test_no_new_unit_escapes_the_install_script() -> None:
    """Neue Units müssen installierbar sein — die Altlast darf nur schrumpfen."""
    gap = _units_on_disk() - _listed_units()
    new_offenders = sorted(gap - _baseline())

    assert not new_offenders, (
        "Diese Units liegen in deploy/systemd, werden aber von "
        "scripts/pi_install_systemd.sh nicht kopiert — auf einem frischen Host "
        f"fehlen sie: {new_offenders}"
    )


def test_baseline_shrinks_but_never_grows() -> None:
    """Wird eine Altlast-Unit nachgetragen, muss die Baseline nachziehen."""
    gap = _units_on_disk() - _listed_units()
    stale = sorted(_baseline() - gap)

    assert not stale, (
        "Diese Units stehen inzwischen im Install-Skript und gehören aus der "
        f"Baseline entfernt (tests/fixtures/systemd_install_gap_baseline.json): {stale}"
    )


def test_the_failure_notifier_is_installable() -> None:
    """Ein OnFailure= auf eine nie installierte Unit ist wirkungslos."""
    assert "kai-unit-failure-notify@.service" in _listed_units()
    assert (_UNIT_DIR / "kai-unit-failure-notify@.service").exists()
