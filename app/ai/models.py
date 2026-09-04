"""Was ein Versuch und was ein Ergebnis ist — inklusive „ich weiß es nicht".

Zwei Begriffe, die der erste LiteLLM-Anlauf vermischt hat:

``AttemptTrace``    EIN Versuch gegen EINEN Upstream. Was angefordert wurde,
                    was tatsächlich geantwortet hat, wie lange es dauerte, was
                    es kostete — oder dass die Kosten unbekannt sind.
``InferenceResult`` die Klammer über alle Versuche eines Aufrufs, plus die
                    Frage, ob das Ergebnis überhaupt etwas bewirken darf.

**Angefordert ist nicht dasselbe wie tatsächlich.** Ein Gateway darf einen Alias
auf ein anderes Modell legen, und ein Anbieter darf hinter demselben Namen etwas
anderes betreiben. ``requested_model`` und ``actual_model`` sind deshalb zwei
Felder, nicht eines. Sind sie identisch, ist das eine Aussage; ist ``actual``
leer, ist das eine andere — und keine der beiden darf als die jeweils andere
gelesen werden.

**UNKNOWN ist nicht 0.** Kosten, die niemand kennt, sind unbekannt, nicht
kostenlos. ``None`` heisst hier durchgängig „nicht belegt", und
:func:`total_cost_usd` gibt genau dann ``None`` zurück, wenn auch nur ein
Versuch unbelegt ist. Eine Summe, die stillschweigend ein paar Versuche als
gratis verbucht, ist kein Budget, sondern ein Gefühl.

Rein: keine Uhr, kein I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.ai.audit import ErrorClass, Purpose
from app.ai.modes import Mode, has_execution_authority
from app.ai.routes import Route

#: Wie ein Aufruf transportiert wurde. Bewusst getrennt vom Anbieter: „direkt
#: gegen OpenAI" und „ueber LiteLLM zu OpenAI" ist derselbe Anbieter, aber ein
#: anderer Beweisweg — und genau der ist beim Shadow-Vergleich die Frage.
Transport = str


@dataclass(frozen=True)
class AttemptTrace:
    """Ein einzelner Versuch gegen einen Upstream."""

    transport: Transport
    requested_model: str
    latency_ms: float
    actual_provider: str = ""
    actual_model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: ``None`` heisst UNBEKANNT, niemals 0.
    cost_usd: float | None = None
    error_class: ErrorClass | None = None
    request_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error_class is None

    @property
    def cost_known(self) -> bool:
        return self.cost_usd is not None

    @property
    def identity_proven(self) -> bool:
        """Hat der Upstream sich selbst benannt?

        Nur wenn Anbieter UND Modell aus der Antwort kommen. Den angeforderten
        Alias als Identitaet zu fuehren waere eine Behauptung ueber etwas, das
        man nicht gemessen hat — im ersten Anlauf stand genau das in der
        Telemetrie und sah wie ein Beweis aus.
        """
        return bool(self.actual_provider) and bool(self.actual_model)

    @property
    def model_substituted(self) -> bool:
        """Hat der Upstream ein anderes Modell geliefert als angefordert?"""
        return self.identity_proven and self.actual_model != self.requested_model


def total_cost_usd(attempts: Sequence[AttemptTrace]) -> float | None:
    """Summe — oder ``None``, sobald ein einziger Versuch unbelegt ist.

    Kein ``sum(a.cost_usd or 0.0 ...)``. Diese eine Zeile war im ersten Anlauf
    der Grund, warum ein Tagesbudget aus lauter Nullen bestand und trotzdem
    ueberschritten wurde.
    """
    if not attempts:
        return None
    if any(a.cost_usd is None for a in attempts):
        return None
    return sum(a.cost_usd or 0.0 for a in attempts)


def cost_known_rate(attempts: Sequence[AttemptTrace]) -> float | None:
    """Anteil der Versuche mit belegten Kosten — ``None`` ohne Versuche."""
    if not attempts:
        return None
    return sum(1 for a in attempts if a.cost_known) / len(attempts)


@dataclass(frozen=True)
class InferenceResult:
    """Das Ergebnis eines Aufrufs samt aller Versuche, die dazu noetig waren."""

    route: Route
    purpose: Purpose
    mode: Mode
    attempts: tuple[AttemptTrace, ...] = ()
    fell_back_to_direct: bool = False
    correlation_id: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].ok

    @property
    def execution_authority(self) -> bool:
        """Darf dieses Ergebnis etwas bewirken?

        Ausschliesslich am Modus, NICHT am Erfolg. Ein geglueckter
        Schatten-Aufruf bleibt ein Schatten — sonst haette ein gutes Ergebnis
        die Graduation ersetzt, und genau das ist die implizite Aktivierung,
        die ADR 0017 ausschliesst.
        """
        return has_execution_authority(self.mode)

    @property
    def total_cost_usd(self) -> float | None:
        return total_cost_usd(self.attempts)

    @property
    def latency_ms(self) -> float:
        return sum(a.latency_ms for a in self.attempts)

    @property
    def identity_proven(self) -> bool:
        """Hat der letzte, zaehlende Versuch seinen Upstream benannt?"""
        return bool(self.attempts) and self.attempts[-1].identity_proven

    @property
    def error_class(self) -> ErrorClass | None:
        return self.attempts[-1].error_class if self.attempts else None


__all__ = [
    "AttemptTrace",
    "InferenceResult",
    "Transport",
    "cost_known_rate",
    "total_cost_usd",
]
