"""Der Nachfolgeschlüssel muss auch auf ``/operator/*`` gelten.

``app/security/auth.py`` akzeptiert seit SENTR-F-008 beide Schlüssel während
eines Rotationsfensters (``api_key`` und ``api_key_next``). Der Operator-Router
prüfte jedoch nur ``api_key``. Wirkung: sobald der Operator den neuen Schlüssel
verteilt, antworten alle ``/operator/*``-Endpunkte 403 — die Rotation bricht ab
und der alte Schlüssel bleibt im Einsatz. Eine Rotation, die man nicht zu Ende
führen kann, ist keine.
"""

from __future__ import annotations

import inspect

from app.api.routers import operator


def _guard_source() -> str:
    """Quelltext des Bearer-Guards ohne Kommentarzeilen."""
    raw = inspect.getsource(operator)
    return "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))


def test_nachfolgeschluessel_wird_geprueft() -> None:
    src = _guard_source()
    assert "api_key_next" in src, (
        "Der Operator-Guard kennt den Nachfolgeschluessel nicht — eine laufende "
        "Rotation wuerde hier mit 403 abbrechen."
    )


def test_beide_schluessel_konstantzeitig_verglichen() -> None:
    src = _guard_source()
    # Kein `==` auf Geheimnissen: der Vergleich muss ueber compare_digest laufen.
    assert src.count("secrets.compare_digest") >= 2


def test_leerer_nachfolgeschluessel_oeffnet_nichts() -> None:
    """``api_key_next=""`` darf keinen Zugang gewähren.

    ``compare_digest(x, "")`` ist für leeres ``x`` wahr — deshalb muss der
    leere Nachfolgeschlüssel vor dem Vergleich ausgeschlossen werden, sonst
    wäre ein leerer Bearer im Einzelschlüssel-Betrieb gültig.
    """
    src = _guard_source()
    assert "api_key_next and secrets.compare_digest" in src
