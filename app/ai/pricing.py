"""Was ein Aufruf gekostet HÄTTE — eine Schätzung, und sie sagt das auch.

Diese Datei ist die einzige Preistabelle im Repo. Vor ihr gab es keine: die
Modellnamen standen in ``app/core/settings.py``, die Rechnung lag beim
Operator, und dazwischen war nichts. Jede Kostenaussage über KAI war deshalb
entweder eine Handrechnung oder eine Behauptung.

**Drei Regeln, die diese Datei zu einer belastbaren Grundlage machen:**

1. **UNKNOWN ist nicht 0.** Ein unbekanntes Modell liefert
   :attr:`CostEstimate.usd` als ``None`` und ``status="COST_UNKNOWN"`` — nie
   ``0.0``. Eine Summe mit unsichtbaren Nullen sieht aus wie eine Abrechnung
   und ist keine. Das ist dieselbe Regel, unter der ``app.ai.budget``
   ``unknown_calls`` zählt statt sie zu nullen.
2. **Schätzung ist keine Abrechnung.** Alles hier sind LISTENPREISE
   (:data:`PRICE_SOURCE`). Der Rechnungsbetrag des Anbieters ist eine andere
   Zahl, und wo beide nebeneinander ausgewiesen werden, bleiben sie getrennt
   benannt. Die einzige im Repo gegen eine echte Rechnung geprüfte Position
   ist ``claude-sonnet-4-6`` (KAI_COST_SURFACE.md:75-84, Abweichung +11 % auf
   einer 5,6-Tage-Stichprobe).
3. **Keine geratenen Namen.** Ein Modellname wird normalisiert (Kleinschreibung,
   Anbieter-Präfix entfernt) und dann EXAKT nachgeschlagen. Ein datierter
   Bezeichner wie ``gpt-4o-2024-08-06`` fällt bewusst auf ``COST_UNKNOWN``,
   statt auf ``gpt-4o`` gebogen zu werden: eine Tabelle, die Namen errät,
   produziert Zahlen, denen niemand nachrechnen kann.

Rein: keine Uhr, kein I/O, kein Netz. Testbar wie ``app.ai.budget``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

#: Version der Tabelle. Sie steht in JEDER Telemetriezeile, die mit ihr
#: gerechnet wurde (``cost_source="list_price:<version>"``) — ohne das wäre
#: eine spätere Preisänderung rückwirkend nicht von einem Rechenfehler zu
#: unterscheiden.
PRICE_TABLE_VERSION: Final = "2026-09-08"

#: Woher die Zahlen stammen. ``list_price`` heisst: öffentlich ausgewiesener
#: Listenpreis, NICHT der abgerechnete Betrag.
PRICE_SOURCE: Final = "list_price"

#: Stand der Erhebung.
PRICE_TABLE_DATE: Final = "2026-09-08"

CostStatus = Literal["OK", "COST_UNKNOWN"]


@dataclass(frozen=True)
class ModelPrice:
    """USD je 1 Mio. Token, Ein- und Ausgabe getrennt."""

    usd_per_1m_input: float
    usd_per_1m_output: float
    #: Gegen eine echte Anbieter-Rechnung geprüft? Nur ``claude-sonnet-4-6``
    #: erfüllt das heute. Alles andere ist Liste, nicht Beleg.
    confirmed: bool = False
    note: str = ""


#: Modell → Preis. Was hier NICHT steht, ist ``COST_UNKNOWN`` — es gibt keinen
#: Rückfallpreis und keinen Durchschnitt. Ein Durchschnitt über unbekannte
#: Modelle wäre genau die unsichtbare Null, gegen die diese Datei steht.
PRICE_TABLE: Final[dict[str, ModelPrice]] = {
    "gpt-4o": ModelPrice(2.50, 10.00, note="Listenpreis; Operator-Bestaetigung offen"),
    "gpt-4o-mini": ModelPrice(0.15, 0.60, note="Listenpreis; Operator-Bestaetigung offen"),
    "claude-sonnet-4-6": ModelPrice(
        3.00,
        15.00,
        confirmed=True,
        note="gegen Anthropic-Rechnung 2026-08 geprueft (KAI_COST_SURFACE.md:75-84)",
    ),
    # WIDERSPRUCH, bewusst stehen gelassen statt still entschieden: der
    # Operator-Auftrag nennt 3,00/15,00 und markiert sie ausdrücklich als
    # »unbestätigt«; der unabhängige Audit KAI_COST_SURFACE.md:86-91 nennt
    # 2,00/10,00. Beide sind unbelegt — keine Rechnung im Repo weist dieses
    # Modell aus, und KAI benutzt es nirgends. Hier steht der Auftragswert,
    # weil die Tabelle EINE Herkunft haben muss; die Abweichung ist damit
    # sichtbar statt weggemittelt. Auflösung nur über eine echte Rechnung.
    "claude-sonnet-5": ModelPrice(
        3.00,
        15.00,
        note="unbestaetigt; KAI_COST_SURFACE.md:86-91 nennt 2.00/10.00 - Rechnung entscheidet",
    ),
    "gemini-2.5-flash": ModelPrice(0.30, 2.50, note="Listenpreis; Operator-Bestaetigung offen"),
    "gemini-2.5-pro": ModelPrice(1.25, 10.00, note="Listenpreis; Operator-Bestaetigung offen"),
}


@dataclass(frozen=True)
class CostEstimate:
    """Kosten eines Aufrufs — oder die ehrliche Auskunft, dass sie fehlen."""

    usd: float | None
    status: CostStatus
    #: Warum unbekannt: ``no_tokens`` | ``unknown_model`` | ``no_model`` |
    #: ``negative_tokens``. Bei ``OK`` leer.
    reason: str = ""
    #: Herkunft der Zahl. Bei ``OK``: ``list_price:<version>``.
    source: str = ""
    #: Der Name, unter dem tatsächlich nachgeschlagen wurde.
    model: str = ""

    @property
    def known(self) -> bool:
        return self.usd is not None


def normalize_model(model: str | None) -> str:
    """Vergleichsform eines Modellnamens: klein, ohne Anbieter-Präfix, getrimmt.

    ``anthropic/claude-sonnet-4-6`` und ``claude-sonnet-4-6`` sind derselbe
    Preis; ``gpt-4o-2024-08-06`` ist es NICHT (siehe Modul-Docstring).
    """
    if not model:
        return ""
    name = str(model).strip().lower()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def resolve_priced_model(
    *, requested_model_alias: str | None = None, actual_model: str | None = None
) -> str:
    """Welcher Name wird bepreist — ``actual_model`` schlägt den Alias.

    KEINE zweite Alias-Tabelle. ``app.ai.config.InferenceSettings.route_aliases``
    bildet Route auf Alias ab (``kai-standard``); was DAHINTER läuft, weiss nur
    der Upstream, und genau dafür trägt jede Telemetriezeile seit v2
    ``actual_model``. Der Alias ist der Rückfall — und ein Alias steht nicht in
    :data:`PRICE_TABLE`, also endet dieser Rückfall ehrlich in
    ``COST_UNKNOWN`` statt in einem geratenen Preis.
    """
    actual = normalize_model(actual_model)
    return actual or normalize_model(requested_model_alias)


def price_for(model: str | None) -> ModelPrice | None:
    """Preis eines Modells oder ``None``. Kein Rückfallwert, kein Durchschnitt."""
    return PRICE_TABLE.get(normalize_model(model))


def estimate_cost_usd(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> CostEstimate:
    """Kosten aus Token und Listenpreis — oder ``COST_UNKNOWN`` mit Grund.

    Args:
        model: Modellname; wird über :func:`normalize_model` nachgeschlagen.
        input_tokens: Eingabetoken; ``None`` heisst UNBEKANNT, nicht 0.
        output_tokens: Ausgabetoken; ``None`` heisst UNBEKANNT, nicht 0.

    Returns:
        :class:`CostEstimate`. ``status="OK"`` nur, wenn Modell UND beide
        Token-Zahlen bekannt sind. Ein Aufruf mit bekanntem Modell aber ohne
        Usage-Block ist unbekannt teuer, nicht kostenlos.
    """
    name = normalize_model(model)
    if not name:
        return CostEstimate(usd=None, status="COST_UNKNOWN", reason="no_model")
    price = PRICE_TABLE.get(name)
    if price is None:
        return CostEstimate(usd=None, status="COST_UNKNOWN", reason="unknown_model", model=name)
    if input_tokens is None or output_tokens is None:
        return CostEstimate(usd=None, status="COST_UNKNOWN", reason="no_tokens", model=name)
    try:
        eingabe = int(input_tokens)
        ausgabe = int(output_tokens)
    except (TypeError, ValueError):
        return CostEstimate(usd=None, status="COST_UNKNOWN", reason="no_tokens", model=name)
    if eingabe < 0 or ausgabe < 0:
        # Negative Token gibt es nicht. Sie als 0 zu verbuchen wäre eine
        # stille Korrektur an Zahlen, die Geld bedeuten.
        return CostEstimate(usd=None, status="COST_UNKNOWN", reason="negative_tokens", model=name)
    usd = (eingabe * price.usd_per_1m_input + ausgabe * price.usd_per_1m_output) / 1_000_000.0
    return CostEstimate(
        usd=round(usd, 8),
        status="OK",
        source=f"{PRICE_SOURCE}:{PRICE_TABLE_VERSION}",
        model=name,
    )


def unconfirmed_models() -> tuple[str, ...]:
    """Modelle, deren Preis noch gegen keine Rechnung geprüft wurde.

    Für den Bericht an den Operator: solange diese Liste nicht leer ist, ist
    jede Summe über sie eine Schätzung — auch wenn sie auf den Cent genau
    aussieht.
    """
    return tuple(sorted(name for name, price in PRICE_TABLE.items() if not price.confirmed))


__all__ = [
    "PRICE_SOURCE",
    "PRICE_TABLE",
    "PRICE_TABLE_DATE",
    "PRICE_TABLE_VERSION",
    "CostEstimate",
    "CostStatus",
    "ModelPrice",
    "estimate_cost_usd",
    "normalize_model",
    "price_for",
    "resolve_priced_model",
    "unconfirmed_models",
]
