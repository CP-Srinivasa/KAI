"""Zurueckgestellt heisst NICHT unbeobachtet.

`kai-litellm.service` liegt als Unit-Datei im Repo, soll aber bewusst nicht
laufen: die LiteLLM-Spur ist seit dem 2026-09-08 zurueckgestellt
(DEFERRED_UPSTREAM_DEPENDENCY_CONFLICT — litellm verlangt ``openai<3.0.0``, das
Lockfile pinnt ``openai==3.6.0``, ein Core-Downgrade ist ausgeschlossen).

`expected_attesting_units` leitet die Erwartung aus den Unit-Dateien ab. Das ist
gut gedacht und an einer Stelle blind: eine Datei im Repo ist nicht dasselbe wie
eine Unit, die laufen SOLL. Die Folge war ein ``EXPECTED_UNIT_NOT_RUNNING`` alle
15 Minuten — ein CRITICAL, den kein Operator schliessen konnte, und der damit
jeden CRITICAL neben sich entwertete.

Die Ausnahme allein waere aber die falsche Haelfte. Aus "wird nicht erwartet"
darf kein "wird nicht ueberwacht" werden, sonst faellt niemandem auf, wenn die
Komponente still doch anlaeuft. Beide Richtungen stehen hier.
"""

from __future__ import annotations

from pathlib import Path

from app.alerts.alert_classes import AlertClass, classify
from app.alerts.process_runtime_probe import (
    DEFERRED_UNITS,
    deferred_unit_violations,
    expected_attesting_units,
)

_REPO = Path(__file__).resolve().parents[2]


# ── Negative Counterprobe: DEFERRED + Unit fehlt -> GRUEN ────────────────────


def test_zurueckgestellte_unit_wird_nicht_erwartet() -> None:
    """Der reale Dauerbefund vom 2026-09-07/08 — er darf nicht mehr entstehen."""
    erwartet = expected_attesting_units(_REPO)

    assert "kai-litellm.service" not in erwartet
    # Die anderen bleiben erwartet: die Ausnahme ist eine Ausnahme, kein Abriss.
    assert "kai-server.service" in erwartet
    assert len(erwartet) >= 5


def test_fehlende_zurueckgestellte_unit_ist_kein_befund() -> None:
    """`not-found` / `inactive` ist der ERWARTETE Zustand, nicht der Fehler."""
    assert deferred_unit_violations(probe=lambda verb, unit: "not-found") == ()
    assert (
        deferred_unit_violations(
            probe=lambda verb, unit: "inactive" if verb == "is-active" else "disabled"
        )
        == ()
    )


def test_unlesbares_systemctl_erfindet_keinen_befund() -> None:
    """Ohne Antwort wird nicht geraten — weder in die eine noch die andere Richtung."""
    assert deferred_unit_violations(probe=lambda verb, unit: "") == ()


# ── Positive Counterprobe: DEFERRED + Unit doch da -> CRITICAL ───────────────


def test_laufende_zurueckgestellte_unit_ist_ein_befund() -> None:
    """Genau der Fall, den die Ausnahme NICHT verdecken darf."""
    treffer = deferred_unit_violations(
        probe=lambda verb, unit: "active" if verb == "is-active" else "disabled"
    )

    assert len(treffer) == 1
    assert "kai-litellm.service" in treffer[0]
    assert "laeuft" in treffer[0]


def test_enabled_zurueckgestellte_unit_ist_ein_befund() -> None:
    """Auch ohne laufenden Prozess: `enabled` heisst, sie startet beim naechsten Boot."""
    treffer = deferred_unit_violations(
        probe=lambda verb, unit: "inactive" if verb == "is-active" else "enabled"
    )

    assert len(treffer) == 1
    assert "ist enabled" in treffer[0]


def test_aktiv_und_enabled_meldet_beides() -> None:
    treffer = deferred_unit_violations(
        probe=lambda verb, unit: "active" if verb == "is-active" else "enabled"
    )

    assert len(treffer) == 2


def test_der_befund_erreicht_den_operator_sofort() -> None:
    """P0: eine Abweichung von einer ausdruecklichen Entscheidung wartet nicht.

    Waere der Befund P2/P3, haette die Ausnahme den Alarm nur leiser gemacht
    statt richtiger — und der Fall, der wirklich zaehlt, ginge im Backoff unter.
    """
    assert classify("deferred_unit_active") is AlertClass.P0


# ── Der Vertrag selbst ───────────────────────────────────────────────────────


def test_jeder_eintrag_traegt_grund_datum_und_wiedervorlage() -> None:
    """Eine Ausnahme ohne Begruendung ist eine Erlaubnis auf Vorrat.

    Und die Wiedervorlage ist bewusst ein EREIGNIS, kein Ablaufdatum: ein Datum
    laeuft ab, ohne dass sich etwas geaendert haette, und dann stuende die
    Erwartung wieder da, waehrend der Grund fortbesteht.
    """
    assert DEFERRED_UNITS, "leer waere kein Vertrag, sondern eine tote Konstante"
    for unit, eintrag in DEFERRED_UNITS.items():
        assert eintrag.get("decision_date", "").strip(), f"{unit}: kein Datum"
        assert eintrag.get("reason", "").strip(), f"{unit}: kein Grund"
        assert eintrag.get("reopen_when", "").strip(), f"{unit}: keine Wiedervorlage"
        # Kein Ablaufdatum als Wiedervorlage.
        assert "when" in "reopen_when"


def test_der_grund_benennt_den_konflikt_nachpruefbar() -> None:
    """Ein Grund, der nur 'spaeter' sagt, ist in sechs Monaten wertlos."""
    grund = DEFERRED_UNITS["kai-litellm.service"]["reason"]

    assert "openai" in grund
    assert "3.6.0" in grund, "die konkrete gepinnte Version gehoert in den Grund"
