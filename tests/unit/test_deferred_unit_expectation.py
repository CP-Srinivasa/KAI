"""Zurueckgestellt heisst NICHT unbeobachtet.

`kai-litellm.service` war vom 2026-09-08 bis 2026-09-14 zurueckgestellt
(DEFERRED_UPSTREAM_DEPENDENCY_CONFLICT — litellm verlangt ``openai<3.0.0``, das
Lockfile pinnt ``openai==3.6.0``). Der separate Transport-Baum aus ADR 0019 hat
den Konflikt aufgeloest, die Research-Route laeuft seit dem 2026-09-11 ueber den
Proxy — die Zurueckstellung ist mit D-276 foermlich aufgehoben. Seitdem ist die
Unit ERWARTET: faellt sie aus, ist das ein HOLD, kein Schweigen.

Der Mechanismus bleibt. `expected_attesting_units` leitet die Erwartung aus den
Unit-Dateien ab; eine Datei im Repo ist aber nicht dasselbe wie eine Unit, die
laufen SOLL. Die naechste bewusst zurueckgestellte Komponente braucht dieselbe
Ausnahme — und dieselbe Gegenprobe, damit aus "wird nicht erwartet" kein "wird
nicht ueberwacht" wird. Beide Richtungen stehen hier, an einem Beispiel-Eintrag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.alerts.process_runtime_probe as probe
from app.alerts.alert_classes import AlertClass, classify
from app.alerts.process_runtime_probe import (
    DEFERRED_UNITS,
    deferred_unit_finding,
    deferred_unit_violations,
    expected_attesting_units,
)

_REPO = Path(__file__).resolve().parents[2]

_BEISPIEL: dict[str, dict[str, str]] = {
    "kai-beispiel.service": {
        "decision_date": "2026-09-14",
        "reason": "Beispiel-Konflikt: paket-x verlangt y<2, das Lockfile pinnt y==3.1.0",
        "reopen_when": "ein vertraeglicher Abhaengigkeitsvertrag existiert",
    },
}


# ── Die Aufhebung vom 2026-09-14 (D-276) ─────────────────────────────────────


def test_litellm_ist_wieder_erwartet() -> None:
    """Der Dauer-P0 `deferred_unit_active` vom 09.–14.09. darf nicht wiederkehren."""
    erwartet = expected_attesting_units(_REPO)

    assert "kai-litellm.service" in erwartet
    assert "kai-server.service" in erwartet
    assert "kai-litellm.service" not in DEFERRED_UNITS


def test_laufende_litellm_unit_ist_kein_befund_mehr() -> None:
    assert deferred_unit_finding(probe=lambda verb, unit: "active") == ""


# ── Negative Counterprobe: DEFERRED + Unit fehlt -> GRUEN ────────────────────


def test_zurueckgestellte_unit_wird_nicht_erwartet(tmp_path: Path, monkeypatch) -> None:
    units = tmp_path / "deploy" / "systemd"
    units.mkdir(parents=True)
    for name in ("kai-server.service", "kai-beispiel.service"):
        (units / name).write_text("ExecStart=python -m app.cli.main trading runtime-exec\n")
    monkeypatch.setattr(probe, "DEFERRED_UNITS", _BEISPIEL)

    erwartet = expected_attesting_units(tmp_path)

    assert erwartet == ("kai-server.service",), "die Ausnahme ist eine Ausnahme, kein Abriss"


def test_fehlende_zurueckgestellte_unit_ist_kein_befund() -> None:
    """`not-found` / `inactive` ist der ERWARTETE Zustand, nicht der Fehler."""
    assert deferred_unit_violations(_BEISPIEL, probe=lambda verb, unit: "not-found") == ()
    assert (
        deferred_unit_violations(
            _BEISPIEL,
            probe=lambda verb, unit: "inactive" if verb == "is-active" else "disabled",
        )
        == ()
    )


def test_unlesbares_systemctl_erfindet_keinen_befund() -> None:
    """Ohne Antwort wird nicht geraten — weder in die eine noch die andere Richtung."""
    assert deferred_unit_violations(_BEISPIEL, probe=lambda verb, unit: "") == ()


# ── Positive Counterprobe: DEFERRED + Unit doch da -> CRITICAL ───────────────


def test_laufende_zurueckgestellte_unit_ist_ein_befund() -> None:
    """Genau der Fall, den die Ausnahme NICHT verdecken darf."""
    treffer = deferred_unit_violations(
        _BEISPIEL, probe=lambda verb, unit: "active" if verb == "is-active" else "disabled"
    )

    assert len(treffer) == 1
    assert "kai-beispiel.service" in treffer[0]
    assert "laeuft" in treffer[0]


def test_enabled_zurueckgestellte_unit_ist_ein_befund() -> None:
    """Auch ohne laufenden Prozess: `enabled` heisst, sie startet beim naechsten Boot."""
    treffer = deferred_unit_violations(
        _BEISPIEL, probe=lambda verb, unit: "inactive" if verb == "is-active" else "enabled"
    )

    assert len(treffer) == 1
    assert "ist enabled" in treffer[0]


def test_aktiv_und_enabled_meldet_beides() -> None:
    treffer = deferred_unit_violations(
        _BEISPIEL, probe=lambda verb, unit: "active" if verb == "is-active" else "enabled"
    )

    assert len(treffer) == 2


def test_der_befund_nennt_beide_auswege() -> None:
    text = deferred_unit_finding(_BEISPIEL, probe=lambda verb, unit: "active")

    assert "foermlich aufheben" in text
    assert "stoppen" in text


def test_der_befund_erreicht_den_operator_sofort() -> None:
    """P0: eine Abweichung von einer ausdruecklichen Entscheidung wartet nicht.

    Waere der Befund P2/P3, haette die Ausnahme den Alarm nur leiser gemacht
    statt richtiger — und der Fall, der wirklich zaehlt, ginge im Backoff unter.
    """
    assert classify("deferred_unit_active") is AlertClass.P0


# ── Der Vertrag selbst ───────────────────────────────────────────────────────


@pytest.mark.parametrize("eintraege", [DEFERRED_UNITS, _BEISPIEL], ids=["aktuell", "beispiel"])
def test_jeder_eintrag_traegt_grund_datum_und_wiedervorlage(
    eintraege: dict[str, dict[str, str]],
) -> None:
    """Eine Ausnahme ohne Begruendung ist eine Erlaubnis auf Vorrat.

    Leer ist zulaessig: dann ist nichts zurueckgestellt. Jeder Eintrag, der
    dazukommt, traegt Datum, Grund und eine Wiedervorlage als EREIGNIS — ein
    Datum liefe ab, ohne dass sich etwas geaendert haette.
    """
    for unit, eintrag in eintraege.items():
        assert eintrag.get("decision_date", "").strip(), f"{unit}: kein Datum"
        assert eintrag.get("reason", "").strip(), f"{unit}: kein Grund"
        assert eintrag.get("reopen_when", "").strip(), f"{unit}: keine Wiedervorlage"
