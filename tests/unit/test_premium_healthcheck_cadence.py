r"""Die Premium-Healthcheck-Kadenz ist ein bewusster Kompromiss — und muss es bleiben.

Gemessen auf dem Pi am 2026-08-18:

    1111 Laeufe an einem Tag  x  3,215 s CPU  =  ~59 min Pi-CPU pro Tag
    Premium-Bridge in derselben Zeit: 0 Events (seit 2026-08-02, 16 Tage)
    Lightning: DISARMED (APP_LN_PAY_ENABLED=false seit 2026-08-06)

Die 60-s-Kadenz war an die 90-s-Heartbeat-Schwelle gekoppelt: Worst-Case-
Erkennung eines toten Listeners 60+90 = 150 s. Bei 300 s sind es 390 s.

Das ist ein ECHTER Preis, kein Gratis-Gewinn. Er ist nur vertretbar, solange die
Bridge ohne Verkehr laeuft. Dieser Test haelt beides fest: den Zahlenwert UND die
Bedingung, unter der er zurueckgenommen werden muss. Ohne den zweiten Teil waere
die Absenkung eine stille Verschlechterung des Waechters -- genau das Muster
(Schwelle gesetzt, Begruendung vergessen), das den Phantom-Close-Breaker vier
Monate zu weit stehen liess.
"""

from __future__ import annotations

import re
from pathlib import Path

TIMER = Path("deploy/systemd/kai-premium-healthcheck.timer")


def _text() -> str:
    assert TIMER.exists(), f"{TIMER} fehlt"
    return TIMER.read_text(encoding="utf-8")


def test_cadence_is_the_agreed_300s() -> None:
    """Ein stiller Sprung zurueck auf 60 s (oder weiter hoch) faellt hier auf."""
    match = re.search(r"^OnUnitActiveSec=(\d+)s$", _text(), re.MULTILINE)
    assert match, "OnUnitActiveSec nicht gefunden"
    assert int(match.group(1)) == 300, (
        f"Kadenz {match.group(1)}s -- Aenderung nur mit Messung und "
        "aktualisierter Begruendung in der Unit-Datei"
    )


def test_cadence_carries_its_measurement() -> None:
    """Eine Kadenz ohne Zahlen ist geraten. Die Messung muss in der Datei stehen."""
    text = _text()
    for token in ("1111", "3,215", "CPU"):
        assert token in text, f"Messgroesse {token!r} fehlt in der Begruendung"


def test_cadence_names_the_price_it_pays() -> None:
    """Die verlangsamte Erkennung muss ausgeschrieben sein, nicht verschwiegen."""
    text = _text()
    assert "390" in text, "Worst-Case-Erkennung (390 s) nicht benannt"
    assert "150" in text, "vorherige Worst-Case-Erkennung (150 s) nicht benannt"


def test_cadence_names_the_condition_for_going_back() -> None:
    """Der Kompromiss gilt nur, solange die Bridge ruht -- das muss dastehen.

    Sonst bleibt die langsamere Erkennung stehen, wenn Premium-Verkehr
    zurueckkommt, und niemand weiss mehr, warum die Zahl so ist.
    """
    text = _text().lower()
    assert "premium-events" in text or "premium events" in text
    assert "lightning" in text
    assert "60 s" in text or "60s" in text, "Rueckkehrwert 60 s nicht genannt"
