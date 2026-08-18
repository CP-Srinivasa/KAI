r"""Die Oracle-Booking-Kadenz ist ein gemessener Kompromiss — mit Rueckfahrkarte.

Gemessen auf dem Pi am 2026-08-18:

    112 Laeufe an einem Tag  x  2,6 s CPU  =  ~5 min Pi-CPU/Tag
    je Lauf ZWEI LND-Abfragen ueber num_max_invoices=1000
    Ergebnis jedes einzelnen Laufes:  booked={'oracle-l402': 0, 'lnurlp': 0}
    externe Einnahmen lifetime: 0 sat  ·  APP_LN_PAY_ENABLED=false seit 06.08.

Es geht um Buchhaltung ueber BEREITS SETTLED Rechnungen, nicht um einen
Zahlungspfad. Eine Stunde Verzoegerung kostet nichts, und ``Persistent=true``
holt einen verpassten Lauf nach -- es kann also auch ueber einen Neustart
nichts verlorengehen.

Wie bei der Premium-Kadenz gilt: die Zahl allein waere eine stille
Verschlechterung. Festgehalten wird deshalb BEIDES -- der Wert und die
Bedingung, unter der er zurueckzunehmen ist. Ohne den zweiten Teil bleibt die
langsamere Buchung stehen, wenn Einnahmen kommen, und niemand weiss mehr,
warum die Zahl so ist. Genau dieses Muster (Schwelle gesetzt, Begruendung
vergessen) liess den Phantom-Close-Breaker vier Monate zu weit stehen.
"""

from __future__ import annotations

import re
from pathlib import Path

TIMER = Path("deploy/systemd/kai-oracle-earnings-booking.timer")


def _text() -> str:
    assert TIMER.exists(), f"{TIMER} fehlt"
    return TIMER.read_text(encoding="utf-8")


def test_cadence_is_the_agreed_60min() -> None:
    match = re.search(r"^OnUnitActiveSec=(\d+)min$", _text(), re.MULTILINE)
    assert match, "OnUnitActiveSec nicht gefunden"
    assert int(match.group(1)) == 60, (
        f"Kadenz {match.group(1)}min -- Aenderung nur mit Messung und "
        "aktualisierter Begruendung in der Unit-Datei"
    )


def test_cadence_carries_its_measurement() -> None:
    text = _text()
    for token in ("112", "2,6 s CPU", "0 sat"):
        assert token in text, f"Messgroesse {token!r} fehlt in der Begruendung"


def test_cadence_names_the_condition_for_going_back() -> None:
    """Sobald Einnahmen fliessen, ist zeitnahe Buchung ihren Preis wieder wert."""
    text = _text()
    assert "10 min" in text, "Rueckkehrwert nicht genannt"
    assert "Einnahmen" in text, "Rueckkehr-Bedingung nicht genannt"


def test_missed_runs_are_still_caught_up() -> None:
    """`Persistent=true` ist der Grund, warum die Senkung nichts verliert."""
    assert re.search(r"^Persistent=true$", _text(), re.MULTILINE), (
        "ohne Persistent= wuerde eine seltenere Kadenz nach einem Neustart "
        "tatsaechlich Laeufe verlieren"
    )
