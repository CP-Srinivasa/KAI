"""Wer läuft, wer entscheidet, und wer läuft nur mit.

Das Gateway führt die Verträge aus Sprint 1 und 2 zusammen: Route und Modus
sagen, WER autoritativ ist; Circuit und Budget sagen, OB überhaupt gerufen
werden darf. Es ruft selbst nichts an — Transport ist ein übergebener Aufruf,
kein Import. Genau deshalb ist diese Datei ohne Netz, ohne Uhr und ohne Prozess
testbar, und genau deshalb bleibt sie klein.

Der Defekt, gegen den die Bauform steht, hiess „riesiger Router-Monolith": im
ersten Anlauf lagen Modusauswahl, Transport, Retry, Telemetrie, Kosten und
Fallback in einer Datei, und keine Regel war einzeln prüfbar.

**Die eine Zusicherung, die alles trägt:** in ``off`` und ``shadow`` ist der
direkte Pfad autoritativ. Ein LiteLLM-Ergebnis kann dort noch so gut sein — es
erreicht den Aufrufer nicht. Erfolg ersetzt keine Graduation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.ai.audit import Purpose
from app.ai.budget import BudgetDecision, BudgetPolicy, BudgetState, decide
from app.ai.circuit import CircuitBook, CircuitKey, CircuitPolicy, circuit_key
from app.ai.models import AttemptTrace, InferenceResult
from app.ai.modes import Mode, resolve_mode
from app.ai.routes import Route, route_for

#: Ein Transportaufruf: führt EINEN Versuch aus und berichtet, was geschah.
#: Er wirft nicht — ein Fehlschlag ist ein ``AttemptTrace`` mit ``error_class``.
#: Wer wirft, umgeht die Telemetrie, und genau das war im ersten Anlauf der
#: Grund, warum 0 von 12.940 Zeilen einen Fehler trugen.
TransportCall = Callable[[], AttemptTrace]

#: Warum ein Pfad gar nicht erst lief.
SkipReason = str

SKIP_CIRCUIT_OPEN: SkipReason = "circuit_open"
SKIP_BUDGET_REJECT: SkipReason = "budget_reject"
SKIP_MODE_OFF: SkipReason = "mode_off"
SKIP_NO_TRANSPORT: SkipReason = "no_transport"


@dataclass(frozen=True)
class GatewayOutcome:
    """Was der Aufruf ergeben hat — beide Pfade getrennt ausgewiesen."""

    route: Route
    purpose: Purpose
    mode: Mode
    budget: BudgetDecision
    circuit: CircuitBook
    direct: InferenceResult | None = None
    litellm: InferenceResult | None = None
    skipped: tuple[SkipReason, ...] = ()
    detail: dict[str, object] = field(default_factory=dict)

    @property
    def authoritative(self) -> InferenceResult | None:
        """Das Ergebnis, das der Aufrufer benutzen darf.

        Nur in ``primary`` UND nur bei Erfolg ist das der LiteLLM-Pfad; sonst
        der direkte. Das ist zugleich der kontrollierte Fallback aus ADR 0017:
        scheitert LiteLLM als autoritativer Transport, tritt der direkte Pfad
        ein, statt den Aufruf scheitern zu lassen.
        """
        if self.mode == "primary" and self.litellm is not None and self.litellm.ok:
            return self.litellm
        return self.direct

    @property
    def shadow(self) -> InferenceResult | None:
        """Der mitlaufende, unverbindliche Pfad — nur im Schattenmodus."""
        return self.litellm if self.mode == "shadow" else None

    @property
    def fell_back(self) -> bool:
        """Sollte LiteLLM autoritativ sein, hat es aber nicht getragen?"""
        return (
            self.mode == "primary"
            and self.litellm is not None
            and not self.litellm.ok
            and self.direct is not None
        )


def _run(
    call: TransportCall,
    *,
    route: Route,
    alias: str,
    book: CircuitBook,
    policy: CircuitPolicy,
    now_s: float,
) -> tuple[AttemptTrace | None, CircuitBook, SkipReason | None]:
    """Ein Versuch unter Circuit-Aufsicht.

    Der Schlüssel wird ZWEIMAL gebildet: vorher grob (nur der Alias ist bekannt),
    nachher fein, sobald der Upstream sich benannt hat. Anders geht es nicht —
    vor dem Aufruf weiss niemand, wer antworten wird. Gebucht wird auf dem
    feinen Schlüssel, damit ein defekter Anbieter nicht den Alias mitnimmt.
    """
    coarse = CircuitKey(route, alias)
    if not book.allows(coarse, now_s=now_s, policy=policy):
        return None, book, SKIP_CIRCUIT_OPEN
    book = book.on_attempt(coarse, now_s=now_s, policy=policy)

    attempt = call()
    precise = circuit_key(route, alias, attempt)
    if not book.allows(precise, now_s=now_s, policy=policy):
        # Der Upstream war schon gesperrt, nur wusste das vor dem Aufruf niemand.
        # Der Versuch zaehlt trotzdem — verschweigen waere eine Luecke in der
        # Telemetrie, und die Sperre bleibt ohnehin bestehen.
        return attempt, book, SKIP_CIRCUIT_OPEN

    book = (
        book.on_success(precise)
        if attempt.ok
        else book.on_failure(precise, now_s=now_s, policy=policy)
    )
    if coarse != precise and attempt.ok:
        book = book.on_success(coarse)
    return attempt, book, None


def execute(
    *,
    purpose: Purpose,
    alias: str,
    direct_call: TransportCall | None,
    litellm_call: TransportCall | None = None,
    per_route: dict[str, object] | None = None,
    ceiling: object = "off",
    circuit: CircuitBook | None = None,
    circuit_policy: CircuitPolicy | None = None,
    budget_policy: BudgetPolicy | None = None,
    daily: BudgetState | None = None,
    monthly: BudgetState | None = None,
    estimated_request_cost_usd: float | None = None,
    now_s: float = 0.0,
    correlation_id: str = "",
) -> GatewayOutcome:
    """Einen Aufruf durch die Control-Plane führen.

    Reihenfolge mit Absicht: erst Budget, dann Circuit, dann Transport. Ein
    abgelehntes Budget darf keinen Aufruf kosten, und ein offener Kreis keinen
    Versuch — beides waere Geld bzw. Last fuer eine Antwort, die man schon hat.
    """
    route = route_for(purpose)
    mode = resolve_mode(route, per_route=per_route, ceiling=ceiling)
    cpolicy = circuit_policy or CircuitPolicy()
    book = circuit or CircuitBook()
    empty = BudgetState(0.0, 0, 0)
    verdict = decide(
        daily=daily or empty,
        monthly=monthly or empty,
        policy=budget_policy or BudgetPolicy(),
        estimated_request_cost_usd=estimated_request_cost_usd,
    )

    skipped: list[SkipReason] = []
    if verdict == "reject":
        return GatewayOutcome(
            route=route,
            purpose=purpose,
            mode=mode,
            budget=verdict,
            circuit=book,
            skipped=(SKIP_BUDGET_REJECT,),
        )

    litellm_result: InferenceResult | None = None
    if mode == "off":
        skipped.append(SKIP_MODE_OFF)
    elif litellm_call is None:
        skipped.append(SKIP_NO_TRANSPORT)
    else:
        attempt, book, skip = _run(
            litellm_call, route=route, alias=alias, book=book, policy=cpolicy, now_s=now_s
        )
        if skip:
            skipped.append(skip)
        if attempt is not None:
            litellm_result = InferenceResult(
                route=route,
                purpose=purpose,
                mode=mode,
                attempts=(attempt,),
                correlation_id=correlation_id,
            )

    direct_result: InferenceResult | None = None
    # Der direkte Pfad laeuft immer, AUSSER LiteLLM ist autoritativ und hat
    # getragen. Im Schatten laeuft er also mit — sonst waere der Vergleich
    # einseitig und der Schattenbetrieb wertlos.
    litellm_carried = mode == "primary" and litellm_result is not None and litellm_result.ok
    if direct_call is None:
        skipped.append(SKIP_NO_TRANSPORT)
    elif not litellm_carried:
        attempt = direct_call()
        direct_result = InferenceResult(
            route=route,
            purpose=purpose,
            mode=mode,
            attempts=(attempt,),
            fell_back_to_direct=mode == "primary",
            correlation_id=correlation_id,
        )

    return GatewayOutcome(
        route=route,
        purpose=purpose,
        mode=mode,
        budget=verdict,
        circuit=book,
        direct=direct_result,
        litellm=litellm_result,
        skipped=tuple(skipped),
    )


__all__ = [
    "SKIP_BUDGET_REJECT",
    "SKIP_CIRCUIT_OPEN",
    "SKIP_MODE_OFF",
    "SKIP_NO_TRANSPORT",
    "GatewayOutcome",
    "SkipReason",
    "TransportCall",
    "execute",
]
