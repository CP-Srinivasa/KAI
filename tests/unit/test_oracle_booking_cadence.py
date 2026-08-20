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


def test_persistent_is_absent_because_it_would_be_a_no_op() -> None:
    """`Persistent=` wirkt laut systemd ausschliesslich bei `OnCalendar=`.

    In #729 stand es hier — und wurde sogar als BEGRUENDUNG dafuer benutzt, dass
    die Kadenz-Senkung nichts verliert. Das war falsch: an einem rein monotonen
    Timer sichert die Angabe nichts zu. Eine falsche Zusicherung im Repo ist
    schlimmer als gar keine, weil sie beim naechsten Mal geglaubt wird.
    """
    text = _text()
    assert not re.search(r"^\s*OnCalendar=", text, re.MULTILINE), (
        "Unit ist absichtlich monoton — wird das geaendert, muss auch diese "
        "Begruendung neu geprueft werden"
    )
    assert not re.search(r"^\s*Persistent=", text, re.MULTILINE), (
        "Persistent= ohne OnCalendar ist wirkungslos und darf hier nicht stehen"
    )


def test_cadence_safety_rests_on_idempotent_booking() -> None:
    """Der ECHTE Grund, warum eine seltenere Kadenz nichts verliert.

    Nicht `Persistent`, sondern Idempotenz: jeder Lauf listet die settled
    Invoices und bucht die noch ungebuchten. Belegt ist das nicht durch diesen
    Kommentar, sondern durch bestehende Tests —
    `test_ln_earnings_booking.py::test_second_run_is_idempotent`,
    `test_ln_earnings_ledger.py::test_append_is_idempotent_per_payment_hash`
    und `::test_record_settled_invoices_filters_and_dedups`.

    Die Unit muss diesen Grund nennen UND seine Grenze: die Nachholbarkeit endet
    am Listing-Fenster `num_max_invoices=1000`. Ohne die Grenze waere es wieder
    eine unbedingte Behauptung.
    """
    text = _text()
    assert "idempotent" in text.lower(), "der tragende Grund fehlt in der Unit"
    assert "num_max_invoices=1000" in text, (
        "die ehrliche Grenze der Nachholbarkeit fehlt — sonst behauptet die Unit "
        "mehr, als der Code hergibt"
    )


def test_initial_trigger_survives_a_restart() -> None:
    """Der Grund, warum diese Unit ueberhaupt angefasst wurde.

    `OnBootSec` ist nach dem Boot verbraucht; `OnUnitActiveSec` verankert sich am
    Service. `kai-tv-auto-promote` hatte genau diese Kombination und lag nach
    einem Restart fuenf Wochen ohne Termin. `OnActiveSec` verankert sich am Timer
    selbst und ueberlebt jeden Neustart.
    """
    text = _text()
    assert re.search(r"^\s*OnActiveSec=60min$", text, re.MULTILINE)
    assert not re.search(r"^\s*OnBootSec=", text, re.MULTILINE), (
        "OnBootSec ersetzt, nicht ergaenzt — sonst bleibt der irrefuehrende "
        "Eindruck bestehen, es liefere einen Restart-Anker"
    )
