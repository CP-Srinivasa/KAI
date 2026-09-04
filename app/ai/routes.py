"""Routing-Absicht: *wofür* ein Aufruf gedacht ist, nicht *womit* er fährt.

Eine Route sagt, welche Art von Arbeit ansteht — Massenverarbeitung, normale
Analyse, längeres Nachdenken, etwas Unverzichtbares, Sprache-zu-Text. Sie sagt
NICHT, welcher Anbieter, welches Modell oder welcher Transport das erledigt.
Genau diese Trennung fehlte im ersten LiteLLM-Anlauf, wo Absicht und Transport
in derselben Tabelle standen und jede Modelländerung eine Policy-Änderung war.

**Kein zweites Routing-SSOT.** ``app.ai.audit.Purpose`` beschreibt seit
D-CORE-001, *wer* ruft (Analyse, Chat, Intent, STT, Consensus) und steht in
jeder Telemetriezeile. Diese Datei erfindet daneben keine zweite Klassifikation,
sondern leitet die Route aus dem Purpose ab. Wer beides unabhängig pflegen
müsste, hätte in drei Monaten zwei Wahrheiten — die Sorte Drift, gegen die
ADR 0017 geschrieben ist.

Rein: keine Uhr, kein I/O, keine Netzwerkkenntnis.
"""

from __future__ import annotations

from typing import Final, Literal, get_args

from app.ai.audit import Purpose

#: Die logische Absicht eines Aufrufs.
#:
#: ``bulk``      viele, billige, fehlertolerante Aufrufe
#: ``standard``  der Normalfall
#: ``reasoning`` längere Ketten, teurere Modelle vertretbar
#: ``critical``  Fehlschlag ist teuer; Fallback wichtiger als Preis
#: ``stt``       Sprache zu Text — eigene Modalität, gleicher Vertrag
Route = Literal["bulk", "standard", "reasoning", "critical", "stt"]

ROUTES: Final[tuple[Route, ...]] = get_args(Route)

#: Purpose → Route. Die einzige Stelle, an der diese Zuordnung steht.
#:
#: ``consensus`` ist ``reasoning``, weil dort mehrere Meinungen gegeneinander
#: gestellt werden und ein billiges Modell die Aussage wertlos macht. ``intent``
#: ist ``critical``: es übersetzt Operator-Absicht in Systemhandlung, und ein
#: stiller Fehlgriff dort ist teurer als jeder Modellpreis.
_PURPOSE_ROUTE: Final[dict[Purpose, Route]] = {
    "analysis": "standard",
    "chat": "standard",
    "intent": "critical",
    "stt": "stt",
    "consensus": "reasoning",
}


def route_for(purpose: Purpose) -> Route:
    """Die Route zu einem Purpose — vollständig, ohne Rückfallwert.

    Bewusst ohne ``.get(..., default)``: käme je ein Purpose hinzu, ohne hier
    eingetragen zu werden, soll das laut auffallen und nicht still als
    ``standard`` durchlaufen. Der Test ueber die Vollstaendigkeit haelt das fest.
    """
    return _PURPOSE_ROUTE[purpose]


def is_route(value: object) -> bool:
    """Ist *value* eine bekannte Route? Fuer Konfigurationswerte von aussen."""
    return isinstance(value, str) and value in ROUTES


__all__ = ["ROUTES", "Route", "is_route", "route_for"]
