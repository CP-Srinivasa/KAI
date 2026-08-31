"""Welche terminale Klasse darf ein Claim tragen — und welche nicht, mit Grund.

Am 2026-08-31 habe ich diese Tabelle für K1 (`00c75a76a2b0e78b`) einmal von
Hand hergeleitet: Definitionen aus `config/prereg_supervision.json` und
`app/research/prereg_maturity.py` gelesen, jede Klasse gegen den Wortlaut
geprüft, vier ausgeschlossen, eine zugelassen. Das war teuer und — schlimmer —
es war **wiederholbar mit anderem Ergebnis**, denn nichts hielt die Herleitung
fest.

Der eigentliche Schaden droht in der anderen Richtung: wenn ein Claim
administrativ geschlossen werden soll, ist die Versuchung gross, eine
vorhandene Klasse *passend zu machen*. Genau das verbietet dieses Modul
mechanisch, indem es zu jeder Ablehnung den Grund mitliefert.

Die Klassen sind NICHT neu. Sie stehen alle bereits im Vertrag; dieses Modul
entscheidet nur, welche davon ein gegebener Sachverhalt tragen kann.
"""

from __future__ import annotations

import pytest

from app.research.terminal_classes import (
    TERMINAL_CLASSES,
    ClaimFacts,
    admissible_terminal_classes,
    recommended_terminal_class,
)


def _k1() -> ClaimFacts:
    """Die Faktenlage von K1 am 2026-08-31, wie gemessen."""
    return ClaimFacts(
        window_closed=True,
        substantive_evaluation_performed=False,
        population_provably_unevaluable=False,
        successor_prereg_id=None,
        successor_terminal_verdict=None,
        previous_decision_state=None,
    )


# -- K1: der Fall, an dem die Tabelle entstanden ist -------------------------


def test_k1_darf_genau_eine_klasse_tragen() -> None:
    options = admissible_terminal_classes(_k1())
    zulaessig = sorted(k for k, (ok, _) in options.items() if ok)

    assert zulaessig == ["INCONCLUSIVE_BY_TIMEOUT"]
    assert recommended_terminal_class(_k1()) == "INCONCLUSIVE_BY_TIMEOUT"


@pytest.mark.parametrize(
    ("klasse", "grundfragment"),
    [
        ("MET", "nicht ausgewertet"),
        ("NOT_MET", "nicht ausgewertet"),
        ("SUPERSEDED", "Nachfolge"),
        ("CLOSED_UNMEASURABLE", "nicht erwiesen"),
        ("SCHEDULED_REVIEW_COMPLETED", "MANUAL_SCHEDULED_REVIEW"),
    ],
)
def test_jede_ablehnung_nennt_ihren_grund(klasse: str, grundfragment: str) -> None:
    """Eine Ablehnung ohne Grund waere nur eine Meinung."""
    ok, grund = admissible_terminal_classes(_k1())[klasse]

    assert ok is False
    assert grundfragment.lower() in grund.lower(), grund


# -- Die einzelnen Regeln, je gegen ihren Vertragswortlaut ------------------


def test_ohne_auswertung_ist_kein_sachverdikt_zulaessig() -> None:
    options = admissible_terminal_classes(_k1())
    assert options["MET"][0] is False
    assert options["NOT_MET"][0] is False


def test_mit_auswertung_wird_das_sachverdikt_zulaessig() -> None:
    """Positivkontrolle: die Sperre haengt an der Auswertung, nicht an Nachsicht."""
    facts = ClaimFacts(
        window_closed=True,
        substantive_evaluation_performed=True,
        population_provably_unevaluable=False,
        successor_prereg_id=None,
        successor_terminal_verdict=None,
        previous_decision_state=None,
    )
    options = admissible_terminal_classes(facts)

    assert options["MET"][0] is True
    assert options["NOT_MET"][0] is True
    # Und dann ist ein Timeout-Abschluss gerade NICHT mehr richtig.
    assert options["INCONCLUSIVE_BY_TIMEOUT"][0] is False


def test_superseded_verlangt_beide_pflichtfelder() -> None:
    """Der Vertrag nennt superseded_by UND successor_terminal_verdict."""
    nur_id = ClaimFacts(
        window_closed=True,
        substantive_evaluation_performed=False,
        population_provably_unevaluable=False,
        successor_prereg_id="b20ef1487ccba99d",
        successor_terminal_verdict=None,
        previous_decision_state=None,
    )
    assert admissible_terminal_classes(nur_id)["SUPERSEDED"][0] is False

    beide = ClaimFacts(
        window_closed=True,
        substantive_evaluation_performed=False,
        population_provably_unevaluable=False,
        successor_prereg_id="b20ef1487ccba99d",
        successor_terminal_verdict="NOT_MET",
        previous_decision_state=None,
    )
    assert admissible_terminal_classes(beide)["SUPERSEDED"][0] is True


def test_closed_unmeasurable_verlangt_einen_beweis_keine_entscheidung() -> None:
    """6751bc33 durfte es, K1 nicht — der Unterschied ist ein Beleg."""
    bewiesen = ClaimFacts(
        window_closed=True,
        substantive_evaluation_performed=False,
        population_provably_unevaluable=True,
        successor_prereg_id=None,
        successor_terminal_verdict=None,
        previous_decision_state=None,
    )
    assert admissible_terminal_classes(bewiesen)["CLOSED_UNMEASURABLE"][0] is True
    assert admissible_terminal_classes(_k1())["CLOSED_UNMEASURABLE"][0] is False


def test_ein_offenes_fenster_kennt_ueberhaupt_keinen_abschluss() -> None:
    offen = ClaimFacts(
        window_closed=False,
        substantive_evaluation_performed=False,
        population_provably_unevaluable=False,
        successor_prereg_id=None,
        successor_terminal_verdict=None,
        previous_decision_state=None,
    )
    options = admissible_terminal_classes(offen)

    assert not any(ok for ok, _ in options.values())
    assert recommended_terminal_class(offen) is None


def test_scheduled_review_completed_haengt_am_vorgaengerzustand() -> None:
    passend = ClaimFacts(
        window_closed=True,
        substantive_evaluation_performed=True,
        population_provably_unevaluable=False,
        successor_prereg_id=None,
        successor_terminal_verdict=None,
        previous_decision_state="MANUAL_SCHEDULED_REVIEW",
    )
    assert admissible_terminal_classes(passend)["SCHEDULED_REVIEW_COMPLETED"][0] is True


# -- Was das Modul ausdruecklich NICHT anbietet -----------------------------


def test_undefinierte_zustaende_stehen_gar_nicht_zur_wahl() -> None:
    """RETIRE und NO_WATCH_REQUIRED tragen im Register woertlich 'Nicht vergeben.'

    Ein Name ohne Definition darf nicht auswaehlbar sein — sonst wird er
    irgendwann benutzt und dabei erfunden.
    """
    assert "RETIRE" not in TERMINAL_CLASSES
    assert "NO_WATCH_REQUIRED" not in TERMINAL_CLASSES


def test_die_empfehlung_ist_nie_mehrdeutig() -> None:
    """Bleiben mehrere Klassen zulaessig, empfiehlt das Modul KEINE."""
    mehrdeutig = ClaimFacts(
        window_closed=True,
        substantive_evaluation_performed=True,
        population_provably_unevaluable=False,
        successor_prereg_id=None,
        successor_terminal_verdict=None,
        previous_decision_state=None,
    )
    options = admissible_terminal_classes(mehrdeutig)
    assert sum(1 for ok, _ in options.values() if ok) > 1
    assert recommended_terminal_class(mehrdeutig) is None
