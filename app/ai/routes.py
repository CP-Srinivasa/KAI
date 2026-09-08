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


#: Die günstigste Stufe. Alles darüber ist eine bewusste Entscheidung, teurer
#: zu fahren — und genau das soll in der Telemetrie stehen, statt sich aus
#: Modellnamen rückschliessen zu lassen.
CHEAPEST_ROUTE: Final[Route] = "bulk"

#: Routen, die eine TEURERE Stufe verlangen als den Normalfall.
#:
#: Bewusst OHNE ``standard`` und ohne ``stt``, obwohl beide formal über
#: :data:`CHEAPEST_ROUTE` liegen. ``standard`` ist der Normalfall — stünde es
#: hier, trüge nahezu jede Zeile einen Eskalationsgrund, und ein Feld, das
#: immer gesetzt ist, sagt nichts mehr. ``stt`` ist eine andere MODALITÄT
#: (Sprache zu Text), keine höhere Qualitätsstufe; es als Eskalation zu
#: buchen wäre eine Kostenaussage, die es nicht gibt.
_ESCALATED: Final[frozenset[str]] = frozenset({"reasoning", "critical"})


def escalation_reason_for(route: str, *, budget_bypassed: bool = False) -> str:
    """Warum dieser Aufruf teurer läuft als nötig — leer heisst: tut er nicht.

    Zwei Gründe, und sie sind nicht dasselbe:

    * ``critical_override`` — das Budget war erschöpft und der Aufruf lief
      TROTZDEM, weil ``critical`` nicht gesperrt wird. Das ist die teuerste
      Sorte Eskalation und muss einzeln auffindbar sein.
    * ``route_<name>`` — die Route verlangt regulär eine teurere Stufe
      (``reasoning``, ``critical``). Kein Vorwurf, nur eine Zuordnung: ohne
      sie liesse sich hinterher nicht sagen, welcher Anteil der Rechnung aus
      Anspruch und welcher aus Masse entstand.

    ``standard`` und ``stt`` sind KEINE Eskalation — siehe :data:`_ESCALATED`.
    """
    if budget_bypassed:
        return "critical_override"
    return f"route_{route}" if route in _ESCALATED else ""


__all__ = [
    "CHEAPEST_ROUTE",
    "ROUTES",
    "Route",
    "escalation_reason_for",
    "is_route",
    "route_for",
]
