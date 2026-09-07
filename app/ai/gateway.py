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

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, sleep

from app.ai.audit import Purpose, record_attempt_trace
from app.ai.budget import BudgetDecision, BudgetEntry, BudgetPolicy, BudgetState, decide
from app.ai.circuit import CircuitBook, CircuitKey, CircuitPolicy, circuit_key
from app.ai.models import AttemptResult, AttemptTrace, InferenceResult
from app.ai.modes import Mode, resolve_mode
from app.ai.retry import RetryPolicy, retry_delay_s, should_retry
from app.ai.routes import Route, route_for

#: Ein Transportaufruf: führt EINEN Versuch aus und berichtet, was geschah.
#: Er wirft nicht — ein Fehlschlag ist ein ``AttemptTrace`` mit ``error_class``.
#: Wer wirft, umgeht die Telemetrie, und genau das war im ersten Anlauf der
#: Grund, warum 0 von 12.940 Zeilen einen Fehler trugen.
TransportCall = Callable[[], AttemptTrace]
type AsyncTransportCall[T] = Callable[[], Awaitable[AttemptResult[T]]]

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
    booked: tuple[BudgetEntry, ...] = ()
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


@dataclass(frozen=True)
class AsyncGatewayOutcome[T]:
    """Typed values plus the same policy/evidence outcome as the sync gateway."""

    gateway: GatewayOutcome
    direct_attempt: AttemptResult[T] | None = None
    litellm_attempts: tuple[AttemptResult[T], ...] = ()

    @property
    def authoritative_attempt(self) -> AttemptResult[T] | None:
        if self.gateway.authoritative is self.gateway.litellm and self.litellm_attempts:
            return self.litellm_attempts[-1]
        return self.direct_attempt

    @property
    def authoritative_value(self) -> T | None:
        attempt = self.authoritative_attempt
        return attempt.value if attempt is not None else None

    @property
    def authoritative_error(self) -> BaseException | None:
        attempt = self.authoritative_attempt
        return attempt.error if attempt is not None else None


def _run(
    call: TransportCall,
    *,
    route: Route,
    alias: str,
    book: CircuitBook,
    policy: CircuitPolicy,
    now_s: float,
    retry_policy: RetryPolicy,
    sleeper: Callable[[float], None],
    jitter: Callable[[], float],
) -> tuple[tuple[AttemptTrace, ...], CircuitBook, SkipReason | None]:
    """Bounded attempts under circuit supervision.

    Der Schlüssel wird ZWEIMAL gebildet: vorher grob (nur der Alias ist bekannt),
    nachher fein, sobald der Upstream sich benannt hat. Anders geht es nicht —
    vor dem Aufruf weiss niemand, wer antworten wird. Gebucht wird auf dem
    feinen Schlüssel, damit ein defekter Anbieter nicht den Alias mitnimmt.
    """
    coarse = CircuitKey(route, alias)
    if not book.allows(coarse, now_s=now_s, policy=policy):
        return (), book, SKIP_CIRCUIT_OPEN

    attempts: list[AttemptTrace] = []
    for attempt_number in range(1, retry_policy.max_attempts + 1):
        book = book.on_attempt(coarse, now_s=now_s, policy=policy)
        attempt = call()
        attempts.append(attempt)
        precise = circuit_key(route, alias, attempt)
        if not book.allows(precise, now_s=now_s, policy=policy):
            return tuple(attempts), book, SKIP_CIRCUIT_OPEN

        book = (
            book.on_success(precise)
            if attempt.ok
            else book.on_failure(precise, now_s=now_s, policy=policy)
        )
        if coarse != precise and attempt.ok:
            book = book.on_success(coarse)
        precise_open = book.state(precise, now_s=now_s, policy=policy) == "open"
        if (
            attempt.ok
            or precise_open
            or not should_retry(attempt)
            or attempt_number >= retry_policy.max_attempts
        ):
            break
        sleeper(retry_delay_s(attempt_number, retry_policy, jitter=jitter))
    return tuple(attempts), book, None


def _refuse_inside_event_loop() -> None:
    """Der synchrone Weg wartet mit ``time.sleep`` -- im Event-Loop waere das fatal.

    Der Backoff dieser Schicht ist echte Wartezeit. In einer Coroutine wuerde
    ``time.sleep`` nicht diesen einen Aufruf verzoegern, sondern den gesamten
    Loop anhalten: jeder Telegram-Handler, jeder laufende HTTP-Request, jeder
    Timer. Das ist genau der Fehler, gegen den Luecke B antritt, und er waere
    still -- man saehe nur, dass „alles manchmal haengt".

    Deshalb wird er hier laut. Wer aus async-Code kommt, nimmt
    :func:`execute_async`; die Politik ist dieselbe, nur die Wartemechanik ist
    die des jeweiligen Ausfuehrungsmodells.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "app.ai.gateway.execute() blockiert den Event-Loop -- execute_async() benutzen"
    )


def execute(
    *,
    purpose: Purpose,
    alias: str,
    direct_call: TransportCall | None,
    litellm_call: TransportCall | None = None,
    per_route: Mapping[str, object] | None = None,
    ceiling: object = "off",
    circuit: CircuitBook | None = None,
    circuit_policy: CircuitPolicy | None = None,
    budget_policy: BudgetPolicy | None = None,
    daily: BudgetState | None = None,
    monthly: BudgetState | None = None,
    estimated_request_cost_usd: float | None = None,
    now_s: float = 0.0,
    correlation_id: str = "",
    retry_policy: RetryPolicy | None = None,
    sleeper: Callable[[float], None] = sleep,
    jitter: Callable[[], float] = lambda: 0.0,
) -> GatewayOutcome:
    """Einen Aufruf durch die Control-Plane führen.

    Reihenfolge mit Absicht: erst Budget, dann Circuit, dann Transport. Ein
    abgelehntes Budget darf keinen Aufruf kosten, und ein offener Kreis keinen
    Versuch — beides waere Geld bzw. Last fuer eine Antwort, die man schon hat.
    """
    _refuse_inside_event_loop()

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
        attempts, book, skip = _run(
            litellm_call,
            route=route,
            alias=alias,
            book=book,
            policy=cpolicy,
            now_s=now_s,
            retry_policy=retry_policy or RetryPolicy(),
            sleeper=sleeper,
            jitter=jitter,
        )
        if skip:
            skipped.append(skip)
        if attempts:
            litellm_result = InferenceResult(
                route=route,
                purpose=purpose,
                mode=mode,
                attempts=attempts,
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
        booked=tuple(
            BudgetEntry.from_attempt(route, attempt)
            for result in (litellm_result, direct_result)
            if result is not None
            for attempt in result.attempts
        ),
    )


async def execute_async[T](
    *,
    purpose: Purpose,
    alias: str,
    direct_call: AsyncTransportCall[T] | None,
    litellm_call: AsyncTransportCall[T] | None = None,
    per_route: Mapping[str, object] | None = None,
    ceiling: object = "off",
    circuit: CircuitBook | None = None,
    circuit_policy: CircuitPolicy | None = None,
    budget_policy: BudgetPolicy | None = None,
    daily: BudgetState | None = None,
    monthly: BudgetState | None = None,
    estimated_request_cost_usd: float | None = None,
    retry_policy: RetryPolicy | None = None,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[], float] = lambda: 0.0,
    clock: Callable[[], float] = monotonic,
    correlation_id: str = "",
    telemetry_path: Path | None = None,
) -> AsyncGatewayOutcome[T]:
    """Async execution mechanics with the same KAI policy as :func:`execute`.

    Only LiteLLM receives this layer's bounded retry. Existing direct-provider
    fallback/retry semantics remain inside the direct callable, which is vital
    for the hard OFF rollback path.
    """
    route = route_for(purpose)
    mode = resolve_mode(route, per_route=per_route, ceiling=ceiling)
    detail: dict[str, object] = {}
    if purpose == "consensus" and mode == "primary":
        mode = "shadow"
        detail["mode_clamped"] = "consensus_max_shadow"

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
    litellm_attempts: list[AttemptResult[T]] = []
    direct_task: asyncio.Task[AttemptResult[T]] | None = None
    if mode == "shadow" and direct_call is not None:
        direct_task = asyncio.ensure_future(direct_call())

    # Das Budget regiert die LiteLLM-AUSGABE, nicht den Altpfad. Ein erschoepftes
    # Tagesbudget schaltet den Transport ab und laesst Analysis, Chat, Intent,
    # STT und Consensus unveraendert direkt weiterlaufen. Waere das hier ein
    # frueher `return`, haette die Kostenbremse mehr Macht ueber den Betrieb als
    # der Modus-Schalter -- und ein Budgetende saehe aus wie ein Ausfall.
    try:
        if verdict == "reject":
            skipped.append(SKIP_BUDGET_REJECT)
        elif mode == "off":
            skipped.append(SKIP_MODE_OFF)
        elif litellm_call is None:
            skipped.append(SKIP_NO_TRANSPORT)
        else:
            coarse = CircuitKey(route, alias)
            now_s = clock()
            if not book.allows(coarse, now_s=now_s, policy=cpolicy):
                skipped.append(SKIP_CIRCUIT_OPEN)
            else:
                policy = retry_policy or RetryPolicy()
                for attempt_number in range(1, policy.max_attempts + 1):
                    now_s = clock()
                    book = book.on_attempt(coarse, now_s=now_s, policy=cpolicy)
                    result = await litellm_call()
                    litellm_attempts.append(result)
                    precise = circuit_key(route, alias, result.trace)
                    if not book.allows(precise, now_s=now_s, policy=cpolicy):
                        skipped.append(SKIP_CIRCUIT_OPEN)
                        break
                    book = (
                        book.on_success(precise)
                        if result.trace.ok
                        else book.on_failure(precise, now_s=now_s, policy=cpolicy)
                    )
                    if coarse != precise and result.trace.ok:
                        book = book.on_success(coarse)

                    state = book.state(precise, now_s=now_s, policy=cpolicy)
                    will_retry = (
                        not result.trace.ok
                        and state != "open"
                        and should_retry(result.trace)
                        and attempt_number < policy.max_attempts
                    )
                    record_attempt_trace(
                        result.trace,
                        correlation_id=correlation_id,
                        purpose=purpose,
                        logical_route=route,
                        mode=mode,
                        role="shadow" if mode == "shadow" else "primary",
                        attempt_number=attempt_number,
                        budget_decision=verdict,
                        circuit_state=state,
                        execution_authority=mode == "primary",
                        schema_status=(
                            "valid"
                            if result.trace.ok
                            else "invalid"
                            if result.trace.error_class == "schema"
                            else None
                        ),
                        outcome=(
                            "fallthrough"
                            if will_retry
                            else "success"
                            if result.trace.ok
                            else "exhausted"
                        ),
                        fallback_from=(
                            "litellm"
                            if mode == "primary" and not result.trace.ok and not will_retry
                            else None
                        ),
                        fallback_to=(
                            "direct"
                            if mode == "primary" and not result.trace.ok and not will_retry
                            else None
                        ),
                        path=telemetry_path,
                    )
                    if not will_retry:
                        break
                    await sleeper(retry_delay_s(attempt_number, policy, jitter=jitter))
    except BaseException:
        # Der Schattenlauf des Direktpfads darf nicht als unbeachtete Task
        # zurueckbleiben: der Aufrufer saehe nur den Fehler von hier, waehrend
        # asyncio spaeter eine zweite, herrenlose Ausnahme meldet.
        if direct_task is not None:
            direct_task.cancel()
            with suppress(BaseException):
                await direct_task
        raise

    litellm_result = (
        InferenceResult(
            route=route,
            purpose=purpose,
            mode=mode,
            attempts=tuple(item.trace for item in litellm_attempts),
            correlation_id=correlation_id,
        )
        if litellm_attempts
        else None
    )
    litellm_carried = mode == "primary" and litellm_result is not None and litellm_result.ok

    direct_attempt: AttemptResult[T] | None = None
    if direct_call is None:
        skipped.append(SKIP_NO_TRANSPORT)
    elif direct_task is not None:
        direct_attempt = await direct_task
    elif not litellm_carried:
        direct_attempt = await direct_call()

    direct_result = (
        InferenceResult(
            route=route,
            purpose=purpose,
            mode=mode,
            attempts=(direct_attempt.trace,),
            fell_back_to_direct=mode == "primary",
            correlation_id=correlation_id,
        )
        if direct_attempt is not None
        else None
    )
    gateway = GatewayOutcome(
        route=route,
        purpose=purpose,
        mode=mode,
        budget=verdict,
        circuit=book,
        direct=direct_result,
        litellm=litellm_result,
        skipped=tuple(skipped),
        booked=tuple(
            BudgetEntry.from_attempt(route, attempt)
            for result in (litellm_result, direct_result)
            if result is not None
            for attempt in result.attempts
        ),
        detail=detail,
    )
    return AsyncGatewayOutcome(
        gateway=gateway,
        direct_attempt=direct_attempt,
        litellm_attempts=tuple(litellm_attempts),
    )


__all__ = [
    "SKIP_BUDGET_REJECT",
    "SKIP_CIRCUIT_OPEN",
    "SKIP_MODE_OFF",
    "SKIP_NO_TRANSPORT",
    "GatewayOutcome",
    "AsyncGatewayOutcome",
    "AsyncTransportCall",
    "SkipReason",
    "TransportCall",
    "execute",
    "execute_async",
]
