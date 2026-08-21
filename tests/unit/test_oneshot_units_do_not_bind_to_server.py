r"""Ratchet: kein ``Type=oneshot`` haengt per ``Requires=`` am kai-server.

``Requires=`` propagiert den Stop. Ein Neustart von ``kai-server`` schiesst
damit jeden gerade laufenden Oneshot ab — SIGTERM mitten im Lauf, Unit endet
``failed (result=signal)``, OnFailure-Notifier meldet einen Defekt, den es nicht
gibt. Gemessen 07.-21.08.2026: 9 solcher Meldungen, davon 6 auf
``kai-paper-trading`` (abgeschnitten mitten in der Order-Buchung) und 3 auf
``kai-shadow-resolver``, der 13-14 min von je 30 laeuft und einen Server-Neustart
darum mit knapp 50 % Wahrscheinlichkeit mitnimmt.

``Wants=`` + ``After=`` erhaelt Startreihenfolge und Nachziehen des Servers,
ohne die Stop-Propagation.

Kommentare werden vor der Pruefung entfernt — die Begruendung IN den Units nennt
``Requires=`` woertlich, und eine reine Textsuche wuerde genau daran haengen
bleiben (Lehre: Struktur-Tests muessen Kommentare strippen).
"""

from __future__ import annotations

from pathlib import Path

_UNIT_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd"


def _directives(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", ";"))
    ]


def test_no_oneshot_requires_kai_server() -> None:
    offenders = []
    for unit in sorted(_UNIT_DIR.glob("*.service")):
        directives = _directives(unit.read_text(encoding="utf-8"))
        if "Type=oneshot" not in directives:
            continue
        if any(d.startswith("Requires=") and "kai-server.service" in d for d in directives):
            offenders.append(unit.name)

    assert offenders == [], (
        f"{offenders} binden per Requires= an kai-server: ein Server-Neustart "
        "erschlaegt sie mitten im Lauf. Wants= + After= verwenden."
    )


def test_the_six_repaired_units_still_order_after_the_server() -> None:
    """Wants= ohne After= waere ein stiller Rueckschritt in der Startreihenfolge."""
    repaired = [
        "kai-auto-annotate-blocked.service",
        "kai-auto-annotate.service",
        "kai-paper-trading.service",
        "kai-shadow-report-oneshot.service",
        "kai-shadow-resolver.service",
        "kai-technical-paper-first-fill.service",
    ]
    for name in repaired:
        directives = _directives((_UNIT_DIR / name).read_text(encoding="utf-8"))
        assert "After=kai-server.service" in directives, name
        assert "Wants=kai-server.service" in directives, name
